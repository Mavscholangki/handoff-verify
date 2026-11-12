"""Thin NewHarness-based driver for the handoff spike.

NewHarness supplies the agent loop, tool registry, model adapters and
conversation persistence.  This module adds only what the spike needs:

* a ``run_tests`` tool and a ``sleep`` tool;
* a wrapper around ``registry.execute`` that records tool calls/results and
  git snapshots;
* a compact event log (``events.jsonl``) next to the NewHarness conversation;
* a dev/takeover runner that can be killed and resumed.

The reference harness is not modified and is not the product being evaluated.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SPIKE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SPIKE_DIR.parent
DEFAULT_RUNS = PROJECT_DIR / "runs"

CONFIG: dict[str, Any] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict[str, Any]:
    path = SPIKE_DIR / "config.toml"
    data: dict[str, Any] = {}
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    env_profile = os.environ.get("HANDOFF_MODEL_PROFILE")
    if env_profile:
        data["model_profile"] = env_profile
    env_home = os.environ.get("NEWHARNESS_HOME")
    if env_home:
        data["newharness_home"] = env_home
    return data


def newharness_home() -> Path:
    return Path(CONFIG.get("newharness_home", r"D:\Projects\NewHarness"))


def add_newharness_path() -> None:
    src = newharness_home() / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items() if k != "attachments"}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _parse_status(status: str) -> list[dict[str, str]]:
    changed: list[dict[str, str]] = []
    for line in status.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:]
        if " -> " in path:
            old, new = path.split(" -> ", 1)
            changed.append({"status": code, "old": old, "path": new})
        else:
            changed.append({"status": code, "path": path})
    return changed


def snapshot(repo: Path) -> dict[str, Any]:
    """Return HEAD/tree/worktree state without touching the real index."""
    head_proc = run_git(repo, "rev-parse", "--verify", "HEAD")
    head = head_proc.stdout.strip() if head_proc.returncode == 0 else None
    status = run_git(repo, "status", "--porcelain=v1").stdout
    changed = _parse_status(status)

    fd, index_path = tempfile.mkstemp(prefix="hcb-index-", suffix=".idx")
    os.close(fd)
    os.unlink(index_path)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    try:
        if head:
            run_git(repo, "read-tree", head, env=env)
        run_git(repo, "add", "-A", env=env)
        tree_proc = run_git(repo, "write-tree", env=env)
        tree = tree_proc.stdout.strip() if tree_proc.returncode == 0 else ""
        diff_proc = run_git(repo, "diff", "--binary", head or "HEAD", env=env)
        diff_text = diff_proc.stdout if diff_proc.returncode == 0 else ""
        stat_proc = run_git(repo, "diff", "--stat", head or "HEAD", env=env)
        diff_stat = stat_proc.stdout.strip()
    finally:
        try:
            os.unlink(index_path)
        except OSError:
            pass
    return {
        "head": head,
        "tree": tree,
        "dirty": bool(changed),
        "changed": changed,
        "diff": diff_text,
        "diff_stat": diff_stat,
    }


class Logger:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.path = run_dir / "events.jsonl"
        self.seq = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            try:
                last = [
                    json.loads(line).get("seq", -1)
                    for line in self.path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.seq = max(last, default=-1) + 1
            except (OSError, json.JSONDecodeError):
                self.seq = 0

    def event(self, event_type: str, **fields: Any) -> str:
        event_id = f"evt-{uuid.uuid4().hex[:10]}"
        payload = {
            "event_id": event_id,
            "seq": self.seq,
            "ts": utc_now(),
            "type": event_type,
        }
        payload.update(jsonable(fields))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            fh.flush()
        self.seq += 1
        return event_id

    def blob(self, text: str, suffix: str = ".diff") -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        path = self.run_dir / "blobs" / f"{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(text, encoding="utf-8")
        return f"blobs/{digest}{suffix}"


def make_tools(repo: Path) -> tuple[Any, Any]:
    def run_tests(command: str = "python -m unittest discover -s tests -q") -> dict[str, Any]:
        """Run the repository test command and return exit code and output.

        Args:
            command: Command to run in the repository root.
        """
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-4000:],
        }

    def sleep(seconds: float = 5.0) -> str:
        """Pause for a few seconds (used to test interruption handling).

        Args:
            seconds: How long to sleep.
        """
        time.sleep(float(seconds))
        return f"slept {seconds}s"

    return run_tests, sleep


def install_tools(registry: Any, repo: Path) -> None:
    run_tests, sleep = make_tools(repo)
    registry.register(run_tests, origin="library", mutating=False)
    registry.register(sleep, origin="library", mutating=False)


def tool_status(name: str, result: dict[str, Any]) -> str:
    if not result.get("ok", False):
        return "failed"
    if result.get("skipped"):
        return "not_executed"
    if name == "run_tests":
        inner = result.get("result")
        if isinstance(inner, dict) and int(inner.get("exit_code", 0)) != 0:
            return "failed"
    return "succeeded"


def wrap_registry(registry: Any, repo: Path, logger: Logger, *, kill_on_sleep: bool = False) -> None:
    original = registry.execute

    async def wrapped(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        call_id = logger.event("tool_started", tool=name, arguments=arguments)
        if name == "sleep" and kill_on_sleep:
            # Kill the process while the tool is running.  The started event is
            # already flushed; no finished event will be written.
            threading.Timer(2.0, lambda: os._exit(1)).start()
        before = snapshot(repo)
        diff_ref = None
        if before["diff"]:
            diff_ref = logger.blob(before["diff"])
        before.pop("diff", None)
        started = time.monotonic()
        try:
            result = await original(name, arguments)
        except BaseException as exc:
            logger.event(
                "tool_crashed",
                call_id=call_id,
                tool=name,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        after = snapshot(repo)
        if after["diff"]:
            diff_ref = logger.blob(after["diff"])
        after.pop("diff", None)
        status = tool_status(name, result)
        logger.event(
            "tool_finished",
            call_id=call_id,
            tool=name,
            status=status,
            arguments=arguments,
            result=result,
            before=before,
            after=after,
            duration_s=round(time.monotonic() - started, 3),
            diff_ref=diff_ref,
        )
        return result

    registry.execute = wrapped  # type: ignore[assignment]


DEV_ADDENDUM = """\
You are working in a small Python repository. Use the run_tests tool to run
`python -m unittest discover -s tests -q`. Do not use any shell. Follow the
user's phase instructions exactly: do not start work that the user says is for
the next person.
"""

TAKEOVER_ADDENDUM = """\
You are taking over an unfinished task in this workspace. The repository is in
`repo/`. Use the run_tests tool to run `python -m unittest discover -s tests -q`.
Do not ask the original developer for help. Complete the remaining work.
"""


def build_model(profile_name: str) -> tuple[Any, dict[str, Any]]:
    add_newharness_path()
    from NewHarness.models.config import load_models_config
    from NewHarness.shell.assembly import make_model

    home = newharness_home()
    cfg = load_models_config(home / "data" / "models.toml")
    profile = cfg.get(profile_name)
    key = profile.resolved_api_key()
    if not key:
        raise SystemExit(f"model profile {profile_name!r} has no resolvable API key")
    if profile.model == "openai":
        from NewHarness.models.openai_model import OpenAIModel

        model = OpenAIModel(
            model=profile.model_name or "",
            api_key=key,
            base_url=profile.model_base_url,
            temperature=float(CONFIG.get("temperature", 0.0)),
            timeout=float(CONFIG.get("timeout_s", 180)),
            reasoning_echo=profile.reasoning_echo,
            capabilities=profile.capabilities(),
            profile_name=profile.name,
        )
    else:
        model = make_model(
            profile.model,
            model_name=profile.model_name,
            base_url=profile.model_base_url,
            api_key=key,
            temperature=float(CONFIG.get("temperature", 0.0)),
            reasoning_echo=profile.reasoning_echo,
            capabilities=profile.capabilities(),
            profile_name=profile.name,
        )
    meta = {
        "profile": profile.name,
        "model": profile.model_name,
        "base_url": profile.model_base_url,
    }
    return model, meta


def build_fake_model():
    add_newharness_path()
    from NewHarness.basic.types import ModelResponse, ToolCall
    from NewHarness.models.fake_model import FakeModel

    return FakeModel(responses=[
        ModelResponse(content=None, tool_calls=[
            ToolCall(id="fake-1", name="read_file", arguments={"path": "README.md"})
        ]),
        ModelResponse(content="fake smoke done", tool_calls=[]),
    ])


def build_registry(repo: Path, allowed: list[str] | None = None):
    add_newharness_path()
    from NewHarness.shell.assembly import build_registry as nh_build_registry

    return nh_build_registry(
        repo,
        allowed=allowed,
        create_files_dir=False,
    )


def conversation_len(store: Any, run_id: str) -> int:
    try:
        return len(store.load(run_id)) + 1
    except Exception:
        return 1


def write_manifest(run_dir: Path, data: dict[str, Any]) -> None:
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def update_manifest(run_dir: Path, **updates: Any) -> None:
    path = run_dir / "manifest.json"
    data: dict[str, Any] = {}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    data.update(jsonable(updates))
    write_manifest(run_dir, data)


async def run_one_turn(
    runner: Any,
    store: Any,
    run_id: str,
    logger: Logger,
    prompt: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    logger.event("user_message", content=prompt)
    started = time.monotonic()
    state["logged"] = conversation_len(store, run_id) if state.get("first") else state.get("logged", 1)
    state["first"] = False
    try:
        result = await runner.run(prompt, conversation_id=run_id)
    except BaseException as exc:
        logger.event("turn_failed", error=f"{type(exc).__name__}: {exc}")
        raise

    new_messages = result.messages[state["logged"]:]
    for msg in new_messages:
        role = str(msg.role)
        if role == "model":
            logger.event(
                "assistant_message",
                content=msg.content or "",
                reasoning=msg.reasoning or "",
                tool_calls=[
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in (msg.tool_calls or [])
                ],
                stats=msg.stats or {},
            )
        elif role == "tool" and getattr(msg, "origin", "") == "synthetic_tool_result":
            logger.event(
                "synthetic_tool_result",
                content=msg.content or "",
                tool_call_id=msg.tool_call_id,
                status="not_executed",
            )
        elif getattr(msg, "origin", "") == "harness":
            logger.event("harness_note", content=msg.content or "")

    state["logged"] = len(result.messages)
    turn = {
        "prompt": prompt,
        "final_text": result.final_text,
        "steps": result.steps,
        "tokens": result.total_tokens,
        "usage": result.usage,
        "duration_s": round(time.monotonic() - started, 3),
    }
    logger.event("turn_finished", **turn)
    return turn


def init_repo(repo: Path) -> None:
    if (repo / ".git").is_dir():
        return
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "spike@example.invalid")
    run_git(repo, "config", "user.name", "handoff spike")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", "initial spike task")


async def dev_mode(args: argparse.Namespace) -> None:
    add_newharness_path()
    from NewHarness.runner import Runner
    from NewHarness.store import ConversationStore

    repo = Path(args.repo).resolve()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else DEFAULT_RUNS / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    init_repo(repo)

    logger = Logger(run_dir)
    initial = snapshot(repo)
    if args.resume:
        logger.event("session_resumed", run_id=args.run_id, tree=initial["tree"])
    else:
        logger.event("session_started", run_id=args.run_id, mode="dev")
        logger.event("repo_initial", tree=initial["tree"], head=initial["head"])
        if args.brief:
            logger.event("task_brief", content=Path(args.brief).read_text(encoding="utf-8"))

    if args.fake_smoke:
        model, model_meta = build_fake_model(), {"profile": "fake", "model": "fake"}
        prompts = ["Read README.md and stop."]
        allowed = ["read_file"]
    else:
        model, model_meta = build_model(args.model_profile)
        prompts = load_prompts(args)
        allowed = [
            "read_file", "write_file", "edit_file", "list_dir",
            "glob_files", "search_text", "outline", "run_tests", "sleep",
        ]

    registry = build_registry(repo, allowed=allowed)
    install_tools(registry, repo)
    wrap_registry(registry, repo, logger, kill_on_sleep=args.kill_on_sleep)
    store = ConversationStore(run_dir / "conversations", workspace_id=args.run_id)
    runner = Runner(
        model,
        registry,
        max_steps=int(args.max_steps),
        max_seconds=float(args.max_seconds),
        store=store,
        stream=False,
        system_prompt=DEV_ADDENDUM,
    )

    manifest_path = run_dir / "manifest.json"
    if args.resume and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "running"
        manifest["resumed_at"] = utc_now()
        manifest["model"] = model_meta
    else:
        manifest = {
            "run_id": args.run_id,
            "mode": "dev",
            "repo": str(repo),
            "model": model_meta,
            "status": "running",
            "started_at": utc_now(),
            "prompts": [],
            "turns": [],
            "max_steps": int(args.max_steps),
            "max_seconds": float(args.max_seconds),
        }
    manifest.setdefault("prompts", [])
    manifest.setdefault("turns", [])
    write_manifest(run_dir, manifest)
    state: dict[str, Any] = {"first": True, "logged": 1}
    total_tokens = int(manifest.get("total_tokens") or 0)
    for prompt in prompts:
        turn = await run_one_turn(runner, store, args.run_id, logger, prompt, state)
        total_tokens += int(turn.get("tokens") or 0)
        manifest["prompts"].append(prompt)
        manifest["turns"].append(turn)
        write_manifest(run_dir, manifest)

    logger.event("session_finished", run_id=args.run_id, status="done")
    manifest["status"] = "done"
    manifest["finished_at"] = utc_now()
    manifest["total_tokens"] = total_tokens
    manifest["final_tree"] = snapshot(repo)["tree"]
    write_manifest(run_dir, manifest)


def load_prompts(args: argparse.Namespace) -> list[str]:
    if args.prompt:
        return list(args.prompt)
    if args.script:
        data = json.loads(Path(args.script).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in data]
        raise SystemExit("script must be a JSON array")
    raise SystemExit("provide --prompt or --script")


async def takeover_mode(args: argparse.Namespace) -> None:
    add_newharness_path()
    from NewHarness.runner import Runner
    from NewHarness.store import ConversationStore

    workspace = Path(args.workspace).resolve()
    repo = Path(args.repo).resolve()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else DEFAULT_RUNS / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger = Logger(run_dir)
    logger.event("session_started", run_id=args.run_id, mode="takeover", condition=args.condition)
    model, model_meta = build_model(args.model_profile)
    allowed = [
        "read_file", "write_file", "edit_file", "list_dir",
        "glob_files", "search_text", "outline", "run_tests",
    ]
    if args.condition == "c2":
        allowed.append("cite")
    registry = build_registry(workspace, allowed=allowed)
    install_tools(registry, repo)
    if args.condition == "c2":
        cite_path = Path(args.citations)
        handoff_path = Path(args.handoff)

        def cite(event_id: str) -> str:
            """Return the cited source snippet for a handoff event id.

            Args:
                event_id: Event id such as evt-xxxxxxxx.
            """
            data = json.loads(cite_path.read_text(encoding="utf-8"))
            entry = data.get(event_id)
            if not entry:
                return f"citation not found: {event_id}"
            return str(entry.get("quote", entry))

        registry.register(cite, origin="library", mutating=False)
    wrap_registry(registry, repo if (repo / ".git").is_dir() else workspace, logger)

    store = ConversationStore(run_dir / "conversations", workspace_id=args.run_id)
    runner = Runner(
        model,
        registry,
        max_steps=int(args.max_steps),
        max_seconds=float(args.max_seconds),
        store=store,
        stream=False,
        system_prompt=TAKEOVER_ADDENDUM,
    )
    brief = Path(args.brief).read_text(encoding="utf-8")
    lines = [
        "You are taking over an unfinished task.",
        "",
        brief,
        "",
    ]
    if args.condition == "c1":
        lines.append("A complete raw record of the previous session is in `raw/transcript.md` and `raw/events.jsonl`. Consult it as needed.")
    elif args.condition == "c2":
        lines.append("A handoff is in `handoff.md` and `handoff.json`. You may call `cite(event_id)` to view a source snippet.")
    else:
        lines.append("No previous session record is provided.")
    lines.append("")
    lines.append("Complete the remaining work in `repo/`. Run the tests with run_tests. When done, summarize what you changed.")

    manifest = {
        "run_id": args.run_id,
        "mode": "takeover",
        "condition": args.condition,
        "workspace": str(workspace),
        "repo": str(repo),
        "model": model_meta,
        "status": "running",
        "started_at": utc_now(),
        "max_steps": int(args.max_steps),
        "max_seconds": float(args.max_seconds),
    }
    write_manifest(run_dir, manifest)
    state: dict[str, Any] = {"first": True, "logged": 1}
    started = time.monotonic()
    try:
        turn = await run_one_turn(runner, store, args.run_id, logger, "\n".join(lines), state)
    except BaseException as exc:
        logger.event("session_failed", error=f"{type(exc).__name__}: {exc}")
        raise
    manifest["status"] = "done"
    manifest["finished_at"] = utc_now()
    manifest["duration_s"] = round(time.monotonic() - started, 3)
    manifest["tokens"] = turn.get("tokens")
    manifest["steps"] = turn.get("steps")
    manifest["final_text"] = turn.get("final_text")
    write_manifest(run_dir, manifest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    dev = sub.add_parser("dev")
    dev.add_argument("--run-id", required=True)
    dev.add_argument("--repo", required=True)
    dev.add_argument("--run-dir")
    dev.add_argument("--script")
    dev.add_argument("--prompt", action="append")
    dev.add_argument("--brief")
    dev.add_argument("--resume", action="store_true")
    dev.add_argument("--kill-on-sleep", action="store_true")
    dev.add_argument("--fake-smoke", action="store_true")
    dev.add_argument("--model-profile", default=CONFIG.get("model_profile", "profile1"))
    dev.add_argument("--max-steps", type=int, default=CONFIG.get("max_steps", 16))
    dev.add_argument("--max-seconds", type=float, default=CONFIG.get("max_seconds", 600))

    take = sub.add_parser("takeover")
    take.add_argument("--run-id", required=True)
    take.add_argument("--workspace", required=True)
    take.add_argument("--repo", required=True)
    take.add_argument("--run-dir")
    take.add_argument("--brief", required=True)
    take.add_argument("--condition", choices=["c0", "c1", "c2"], required=True)
    take.add_argument("--handoff")
    take.add_argument("--citations")
    take.add_argument("--model-profile", default=CONFIG.get("model_profile", "profile1"))
    take.add_argument("--max-steps", type=int, default=CONFIG.get("max_steps", 16))
    take.add_argument("--max-seconds", type=float, default=CONFIG.get("max_seconds", 600))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global CONFIG
    CONFIG = load_config()
    args = parse_args(argv)
    if args.mode == "dev":
        asyncio.run(dev_mode(args))
    elif args.mode == "takeover":
        asyncio.run(takeover_mode(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
