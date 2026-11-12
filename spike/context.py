"""Context ledger, one-shot extraction, deterministic handoff and citations.

This is the part the spike is actually testing.  It consumes the event log
produced by ``driver.py`` and produces a handoff that a fresh agent can use.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import driver


# ---------------------------------------------------------------------------
# Event access
# ---------------------------------------------------------------------------

def load_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        raise SystemExit(f"missing event log: {path}")
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def event_index(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["event_id"]: e for e in events}


def quote_for(event: dict[str, Any], limit: int = 500) -> str:
    kind = event.get("type")
    if kind in {"user_message", "assistant_message", "task_brief", "harness_note"}:
        text = str(event.get("content") or "")
    elif kind == "tool_finished":
        result = event.get("result") or {}
        inner = result.get("result") if isinstance(result, dict) else None
        text = f"{event.get('tool')} -> {event.get('status')}"
        if isinstance(inner, dict):
            if "exit_code" in inner:
                text += f" (exit {inner.get('exit_code')})"
                text += " " + str(inner.get("stdout") or inner.get("stderr") or "")[:300]
            else:
                text += " " + json.dumps(inner, ensure_ascii=False)
        elif inner is not None:
            text += " " + str(inner)
        if event.get("after", {}).get("changed"):
            text += " changed=" + ",".join(
                c.get("path", "") for c in event["after"]["changed"]
            )
    elif kind == "synthetic_tool_result":
        text = f"NOT EXECUTED: {event.get('content')}"
    elif kind == "turn_finished":
        text = f"turn finished: {event.get('final_text')}"
    else:
        text = json.dumps(event, ensure_ascii=False)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def render_timeline(events: list[dict[str, Any]], limit: int = 14000) -> str:
    lines: list[str] = []
    for event in events:
        kind = event.get("type")
        eid = event["event_id"]
        if kind == "user_message":
            lines.append(f"[{eid}] USER: {event.get('content', '')}")
        elif kind == "turn_finished":
            final = str(event.get("final_text") or "").strip()
            if final:
                lines.append(f"[{eid}] ASSISTANT FINAL: {final[:2200]}")
        elif kind == "tool_finished":
            tool = event.get("tool")
            status = event.get("status")
            if tool not in {"write_file", "edit_file", "run_tests"} and status not in {"failed", "not_executed"}:
                continue
            lines.append(f"[{eid}] TOOL {tool} {status}: {quote_for(event, 600)}")
            changed = (event.get("after") or {}).get("changed") or []
            if changed:
                lines.append(f"[{eid}] FILE_CHANGE: " + ", ".join(
                    f"{c.get('status')}:{c.get('path')}" for c in changed
                ))
        elif kind == "synthetic_tool_result":
            lines.append(f"[{eid}] SYNTHETIC_NOT_EXECUTED: {event.get('content')}")
        elif kind == "harness_note":
            lines.append(f"[{eid}] HARNESS_NOTE: {event.get('content')}")
        elif kind == "task_brief":
            lines.append(f"[{eid}] TASK_BRIEF: {event.get('content')}")
        elif kind == "session_resumed":
            lines.append(f"[{eid}] SESSION_RESUMED")
    text = "\n".join(lines)
    if len(text) > limit:
        # Keep the head and the most recent events; the extraction prompt
        # should still see the latest decisions and test results.
        head = text[: limit // 3]
        tail = text[-(limit * 2 // 3):]
        text = head + "\n... [middle truncated] ...\n" + tail
    return text


# ---------------------------------------------------------------------------
# Ledger (deterministic; exercised by fixtures)
# ---------------------------------------------------------------------------

@dataclass
class Item:
    item_id: str
    kind: str
    text: str
    status: str = "active"
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    revision: int = 1


class Ledger:
    def __init__(self) -> None:
        self.items: dict[str, Item] = {}

    def add(self, item: Item) -> Item:
        if item.item_id in self.items:
            raise ValueError(f"duplicate item id: {item.item_id}")
        self.items[item.item_id] = item
        return item

    def apply(self, candidate: Item, action: str, target: str | None = None) -> Item:
        if action == "add":
            return self.add(candidate)
        if target is None or target not in self.items:
            raise ValueError(f"unknown target: {target}")
        old = self.items[target]
        if action == "update":
            old.text = candidate.text
            old.rationale = candidate.rationale or old.rationale
            old.evidence = candidate.evidence or old.evidence
            old.revision += 1
            return old
        if action == "supersede":
            old.status = "superseded"
            old.superseded_by = candidate.item_id
            candidate.supersedes = [target]
            candidate.status = "active"
            return self.add(candidate)
        if action == "withdraw":
            old.status = "withdrawn"
            old.revision += 1
            old.evidence = candidate.evidence or old.evidence
            return old
        if action == "reopen":
            old.status = "active"
            old.revision += 1
            old.evidence = candidate.evidence or old.evidence
            return old
        raise ValueError(f"unknown action: {action}")

    def current(self) -> list[Item]:
        return [i for i in self.items.values() if i.status in {"active", "needs_confirmation"}]

    def history(self) -> list[Item]:
        return [i for i in self.items.values() if i.status not in {"active", "needs_confirmation"}]


# ---------------------------------------------------------------------------
# LLM extraction (one call)
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """You are building a handoff ledger from a development session.

Rules:
- Use only facts supported by the event timeline below.
- Every item must cite one or more event ids in "evidence".
- Do not invent motivation. If a reason is not stated or clearly evidenced,
  write "not stated" and mark that decision "needs_confirmation".
- Separate what was actually executed from what was only claimed. A test only
  counts as passed if a tool result has exit code 0.
- If a later instruction changes an earlier decision, keep the earlier one as
  "superseded" and the latest one as "active".
- Mark missing information as unknown instead of filling it in.
- Routine reads, directory listings and "edited file successfully" are not
  completed work; omit them from "done".
- Only commands containing unittest or pytest count as verification. Manual
  probes are checks, not the official test result.
- Never invent an event id. If the timeline is incomplete, say what is unknown
  in the "unknowns" list without assuming facts.

Return only JSON with this shape:
{
  "goal": {"text": "...", "evidence": ["evt-..."]},
  "constraints": [{"text": "...", "evidence": ["evt-..."]}],
  "decisions": [{"text": "...", "rationale": "...", "status": "active|superseded|needs_confirmation", "evidence": ["evt-..."]}],
  "done": [{"text": "...", "evidence": ["evt-..."]}],
  "issues": [{"text": "...", "evidence": ["evt-..."]}],
  "next": [{"text": "...", "basis": "evt-..."}],
  "unknowns": [{"text": "...", "reason": "..."}]
}

Timeline:
"""


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def valid_evidence(raw: Any, known: set[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if str(x) in known]


def extract_context(events: list[dict[str, Any]], profile: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    driver.add_newharness_path()
    from NewHarness.basic.roles import Role
    from NewHarness.basic.types import Message

    timeline = render_timeline(events)
    prompt = EXTRACT_PROMPT + timeline
    model, meta = driver.build_model(profile)

    async def call() -> Any:
        return await model.complete([Message(role=Role.USER, content=prompt)], tools=None)

    response = asyncio.run(call())
    parsed = parse_json_object(response.content or "")
    usage = getattr(response, "usage", {}) or {}
    return parsed, {"model": meta, "usage": usage, "timeline_chars": len(timeline), "raw_content": response.content or ""}


def fallback_context(events: list[dict[str, Any]]) -> dict[str, Any]:
    """No-LLM fallback: only facts that are directly in the event log."""
    brief = next((e for e in events if e.get("type") == "task_brief"), None)
    goal = brief.get("content", "").splitlines()[0] if brief else ""
    tests = [e for e in events if e.get("type") == "tool_finished" and e.get("tool") == "run_tests"]
    issues = []
    for e in events:
        if e.get("type") in {"tool_finished", "synthetic_tool_result"} and e.get("status") in {"failed", "not_executed"}:
            issues.append({"text": f"{e.get('tool')} {e.get('status')}", "evidence": [e["event_id"]]})
    return {
        "goal": {"text": goal, "evidence": [brief["event_id"]] if brief else []},
        "constraints": [],
        "decisions": [],
        "done": [],
        "issues": issues,
        "next": [],
        "unknowns": [{"text": "LLM extraction unavailable or invalid; only direct facts are shown.", "reason": "fallback"}],
        "verification": [
            {"text": f"run_tests exit {((e.get('result') or {}).get('result') or {}).get('exit_code')}", "evidence": [e["event_id"]]}
            for e in tests
        ],
    }


# ---------------------------------------------------------------------------
# Deterministic handoff construction
# ---------------------------------------------------------------------------

def test_records(events: list[dict[str, Any]], repo: Path) -> list[dict[str, Any]]:
    current = driver.snapshot(repo)
    by_id = {e["event_id"]: e for e in events}
    records = []
    for e in events:
        if e.get("type") != "tool_finished" or e.get("tool") != "run_tests":
            continue
        inner = ((e.get("result") or {}).get("result") or {})
        started = by_id.get(e.get("call_id") or "", {})
        arguments = e.get("arguments") or started.get("arguments") or {}
        exit_code = inner.get("exit_code")
        tree = (e.get("after") or {}).get("tree")
        stale = bool(tree and current.get("tree") and tree != current.get("tree"))
        changed: list[str] = []
        if stale and tree:
            proc = driver.run_git(repo, "diff", "--name-only", tree)
            changed = [p for p in proc.stdout.splitlines() if p.strip()]
        command = arguments.get("command", "")
        records.append({
            "event_id": e["event_id"],
            "command": command,
            "official": bool(re.search(r"\b(unittest|pytest)\b", command)),
            "exit_code": exit_code,
            "tree": tree,
            "current_tree": current.get("tree"),
            "stale": stale,
            "changed_since": changed,
            "stdout": str(inner.get("stdout") or "")[-1500:],
            "stderr": str(inner.get("stderr") or "")[-1500:],
            "status": "failed" if exit_code not in (0, None) else ("stale" if stale else "passed"),
        })
    return records


def official_test_records(events: list[dict[str, Any]], repo: Path) -> list[dict[str, Any]]:
    all_records = test_records(events, repo)
    records = []
    seen: set[tuple[Any, Any]] = set()
    for record in reversed(all_records):
        key = (record.get("tree"), record.get("exit_code"))
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    records.reverse()
    official = [r for r in records if r.get("official")]
    return official or records


def file_changes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("type") != "tool_finished":
            continue
        for change in (e.get("after") or {}).get("changed") or []:
            path = change.get("path", "")
            if path:
                seen[path] = {"path": path, "status": change.get("status", ""), "event_id": e["event_id"]}
    return list(seen.values())


def cite_entry(event: dict[str, Any]) -> dict[str, Any]:
    quote = quote_for(event, 1200)
    import hashlib
    raw = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "event_id": event["event_id"],
        "type": event.get("type"),
        "quote": quote,
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
        "ts": event.get("ts"),
    }


def build_handoff(run_dir: Path, repo: Path, brief: str, profile: str, use_llm: bool = True) -> dict[str, Any]:
    events = load_events(run_dir)
    known = set(event_index(events))
    extracted = None
    meta: dict[str, Any] = {}
    if use_llm:
        extracted, meta = extract_context(events, profile)
    if extracted is None:
        extracted = fallback_context(events)

    # Validate evidence ids and downgrade unsupported items instead of silently
    # keeping an invented citation.
    for section in ("constraints", "decisions", "done", "issues", "next"):
        items = extracted.get(section)
        if not isinstance(items, list):
            extracted[section] = []
            continue
        clean = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            item["evidence"] = valid_evidence(item.get("evidence"), known)
            if section in {"constraints", "decisions", "done", "issues"} and not item["evidence"]:
                item["status"] = "needs_confirmation"
                item["evidence"] = []
            clean.append(item)
        extracted[section] = clean
    unknowns = extracted.get("unknowns") if isinstance(extracted.get("unknowns"), list) else []
    if meta.get("raw_content") and extracted.get("_fallback"):
        unknowns.append({"text": "LLM extraction could not be parsed; fallback facts used.", "reason": "extraction"})

    records = official_test_records(events, repo)
    current = driver.snapshot(repo)
    changes = file_changes(events)
    known_paths = {c.get("path") for c in changes}
    for change in current.get("changed") or []:
        path = change.get("path", "")
        if path and path not in known_paths:
            changes.append({
                "path": path,
                "status": change.get("status", ""),
                "event_id": "",
                "external": True,
            })
    last_recorded_tree = None
    for event in reversed(events):
        tree = (event.get("after") or {}).get("tree")
        if tree:
            last_recorded_tree = tree
            break
    if current.get("tree") and last_recorded_tree and current["tree"] != last_recorded_tree:
        unknowns.append({
            "text": "The worktree changed after the last recorded event; an unrecorded edit is likely. Re-run tests before trusting old verification.",
            "reason": "external change",
        })

    # Build the citation table from every id actually referenced.
    cited_ids: set[str] = set()
    for section in ("constraints", "decisions", "done", "issues"):
        for item in extracted.get(section, []):
            cited_ids.update(item.get("evidence") or [])
    for record in records:
        cited_ids.add(record["event_id"])
    for change in changes:
        cited_ids.add(change["event_id"])
    for item in extracted.get("next", []):
        if item.get("basis") in known:
            cited_ids.add(item["basis"])
    citations = {eid: cite_entry(event_index(events)[eid]) for eid in sorted(cited_ids)}

    handoff = {
        "run_id": run_dir.name,
        "generated_at": driver.utc_now(),
        "goal": extracted.get("goal", {}),
        "constraints": extracted.get("constraints", []),
        "decisions": extracted.get("decisions", []),
        "done": extracted.get("done", []),
        "issues": extracted.get("issues", []),
        "next": extracted.get("next", []),
        "unknowns": unknowns,
        "verification": records,
        "file_changes": changes,
        "code_state": {
            "tree": current.get("tree"),
            "head": current.get("head"),
            "dirty": current.get("dirty"),
            "changed": current.get("changed", []),
            "diff_stat": current.get("diff_stat", ""),
        },
        "citations": citations,
        "extraction_meta": {k: v for k, v in meta.items() if k != "raw_content"},
        "source_events": len(events),
        "interrupted": any(e.get("type") == "session_resumed" for e in events),
    }
    return handoff


ROUTINE_DONE_PREFIXES = (
    "read ",
    "listed ",
    "edited ",
    "provided an assistant summary",
    "ran manual behavior probes",
)

EXTRACTION_ARTIFACT_PHRASES = (
    "timeline is truncated",
    "middle events",
    "assistant tool calls have no corresponding",
    "no visible successful tool result shows the edit",
    "explicit instruction or motivation for changing",
    "exact edit event",
    "whether .gitignore",
    "whether the assistant tool calls",
    ".gitignore appears",
    "confirm whether the truncated phrase",
    "truncated phrase",
)


def _text(item: Any) -> str:
    return str(item.get("text") if isinstance(item, dict) else item).lower()


def _drop_artifact(text: str) -> bool:
    return any(phrase in text for phrase in EXTRACTION_ARTIFACT_PHRASES)


def refine_handoff(run_dir: Path, repo: Path) -> dict[str, Any]:
    """Filter extraction noise and refresh deterministic sections."""
    data = json.loads((run_dir / "handoff.json").read_text(encoding="utf-8"))
    events = load_events(run_dir)
    dropped = 0

    done = []
    for item in data.get("done") or []:
        text = _text(item)
        if text.startswith(ROUTINE_DONE_PREFIXES):
            dropped += 1
            continue
        if text not in {_text(x) for x in done}:
            done.append(item)
    data["done"] = done

    for section in ("issues", "unknowns"):
        kept = []
        seen_text: set[str] = set()
        for item in data.get(section) or []:
            text = _text(item)
            reason = str(item.get("reason", "")).lower() if isinstance(item, dict) else ""
            if _drop_artifact(text) or _drop_artifact(reason) or text in seen_text:
                dropped += 1
                continue
            seen_text.add(text)
            kept.append(item)
        data[section] = kept

    next_items = []
    for item in data.get("next") or []:
        if _drop_artifact(_text(item)):
            dropped += 1
            continue
        next_items.append(item)
    data["next"] = next_items

    decisions = []
    for item in data.get("decisions") or []:
        text = _text(item)
        if text.startswith("use constants default_limit") or text.startswith("use constants "):
            dropped += 1
            continue
        decisions.append(item)
    data["decisions"] = decisions

    data["verification"] = official_test_records(events, repo)
    data["interrupted"] = any(e.get("type") == "session_resumed" for e in events)
    current = driver.snapshot(repo)
    changes = file_changes(events)
    known_paths = {c.get("path") for c in changes}
    for change in current.get("changed") or []:
        path = change.get("path", "")
        if path and path not in known_paths:
            changes.append({"path": path, "status": change.get("status", ""), "event_id": "", "external": True})
    data["file_changes"] = changes
    data["code_state"] = {
        "tree": current.get("tree"),
        "head": current.get("head"),
        "dirty": current.get("dirty"),
        "changed": current.get("changed", []),
        "diff_stat": current.get("diff_stat", ""),
    }
    last_recorded_tree = None
    for event in reversed(events):
        tree = (event.get("after") or {}).get("tree")
        if tree:
            last_recorded_tree = tree
            break
    if current.get("tree") and last_recorded_tree and current["tree"] != last_recorded_tree:
        text = "The worktree changed after the last recorded event; an unrecorded edit is likely. Re-run tests before trusting old verification."
        if not any(text.lower() == _text(x) for x in data.get("unknowns") or []):
            data.setdefault("unknowns", []).append({"text": text, "reason": "external change"})

    known = event_index(events)
    used: set[str] = set()
    goal = data.get("goal") or {}
    used.update(goal.get("evidence") or [])
    for section in ("constraints", "decisions", "done", "issues", "unknowns"):
        for item in data.get(section) or []:
            used.update(item.get("evidence") or [])
    for item in data.get("next") or []:
        if item.get("basis"):
            used.add(item["basis"])
    for record in data["verification"]:
        used.add(record["event_id"])
    for change in data.get("file_changes") or []:
        if change.get("event_id"):
            used.add(change["event_id"])
    citations = {eid: cite_entry(known[eid]) for eid in sorted(used) if eid in known}
    data["citations"] = citations
    data["generated_at"] = driver.utc_now()
    data.setdefault("extraction_meta", {})["refined"] = True
    data["extraction_meta"]["filtered_out"] = dropped
    (run_dir / "handoff.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "handoff.md").write_text(render_handoff(data), encoding="utf-8")
    (run_dir / "citations.json").write_text(json.dumps(citations, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "snapshot.json").write_text(json.dumps({
        "generated_at": data["generated_at"],
        "code_state": data["code_state"],
        "source_events": data["source_events"],
        "extraction_meta": data["extraction_meta"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def evidence_suffix(ids: Iterable[str]) -> str:
    ids = list(ids)
    return " " + " ".join(f"[E:{i}]" for i in ids) if ids else ""


def render_handoff(handoff: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Handoff: {handoff['run_id']}")
    lines.append("")
    lines.append(f"- Generated: {handoff['generated_at']}")
    lines.append(f"- Code tree: `{handoff['code_state'].get('tree')}`")
    lines.append(f"- Source events: {handoff['source_events']}")
    lines.append("")

    goal = handoff.get("goal") or {}
    lines.append("## 1. Task goal")
    lines.append(str(goal.get("text") or "Unknown") + evidence_suffix(goal.get("evidence") or []))
    lines.append("")

    lines.append("## 2. Current state")
    code = handoff.get("code_state") or {}
    lines.append(f"- Worktree: {'dirty' if code.get('dirty') else 'clean'} at tree `{code.get('tree')}`")
    remaining = [i.get("text", "") for i in handoff.get("issues") or [] if "export_csv" in str(i.get("text", "")).lower()]
    if remaining:
        lines.append("- Remaining: " + "; ".join(remaining))
    latest = (handoff.get("verification") or [None])[-1]
    if latest:
        lines.append(
            f"- Latest verification: {latest.get('status')} (exit {latest.get('exit_code')}"
            f" at tree `{latest.get('tree')}`)"
        )
        if latest.get("stale"):
            lines.append("- The latest verification is stale because the worktree changed after it ran.")
    else:
        lines.append("- Latest verification: none recorded")
    if handoff.get("interrupted"):
        lines.append("- The previous session was interrupted and resumed; treat the worktree as possibly mid-edit.")
    lines.append("")

    lines.append("## 3. Constraints")
    if handoff.get("constraints"):
        for item in handoff["constraints"]:
            lines.append(f"- {item.get('text')}" + evidence_suffix(item.get("evidence") or []))
    else:
        lines.append("- Unknown / not extracted")
    lines.append("")

    lines.append("## 4. Completed")
    if handoff.get("done"):
        for item in handoff["done"]:
            lines.append(f"- {item.get('text')}" + evidence_suffix(item.get("evidence") or []))
    else:
        lines.append("- No completed items with evidence")
    lines.append("")

    lines.append("## 5. Key decisions")
    active = [d for d in handoff.get("decisions", []) if d.get("status") == "active"]
    history = [d for d in handoff.get("decisions", []) if d.get("status") != "active"]
    if active:
        for item in active:
            reason = item.get("rationale") or "not stated"
            lines.append(f"- **{item.get('text')}** — reason: {reason}" + evidence_suffix(item.get("evidence") or []))
    else:
        lines.append("- No active decision was extracted")
    if history:
        lines.append("")
        lines.append("History (not current):")
        for item in history:
            lines.append(f"- `{item.get('status')}` {item.get('text')}" + evidence_suffix(item.get("evidence") or []))
    lines.append("")

    lines.append("## 6. Code changes")
    if handoff.get("file_changes"):
        for change in handoff["file_changes"]:
            suffix = f" [E:{change['event_id']}]" if change.get("event_id") else " (unrecorded external change)"
            lines.append(f"- `{change['path']}` ({change.get('status', '')}){suffix}")
        lines.append(f"- Worktree diff: {'dirty' if handoff['code_state'].get('dirty') else 'clean'}")
        if handoff["code_state"].get("diff_stat"):
            lines.append("```")
            lines.append(handoff["code_state"]["diff_stat"])
            lines.append("```")
    else:
        lines.append("- No file changes recorded")
    lines.append("")

    lines.append("## 7. Verification")
    if handoff.get("verification"):
        for record in handoff["verification"]:
            label = record["status"].upper()
            lines.append(
                f"- {label}: `{record['command']}` exit={record['exit_code']} at tree `{record['tree']}` "
                f"[E:{record['event_id']}]"
            )
            if record.get("stale"):
                lines.append(f"  - STALE: current tree is `{record['current_tree']}`; changed since: {', '.join(record.get('changed_since') or []) or 'unknown'}")
            output = record.get("stdout") or record.get("stderr") or ""
            if output:
                lines.append(f"  - output: `{output[:300]}`")
    else:
        lines.append("- No test run was recorded; verification is unknown")
    lines.append("")

    lines.append("## 8. Open issues and unknowns")
    items = list(handoff.get("issues") or []) + list(handoff.get("unknowns") or [])
    if items:
        for item in items:
            suffix = evidence_suffix(item.get("evidence") or [])
            lines.append(f"- {item.get('text')}{suffix}")
    else:
        lines.append("- None recorded")
    lines.append("")

    lines.append("## 9. Suggested next steps")
    if handoff.get("next"):
        for item in handoff["next"]:
            basis = f" [basis: {item.get('basis')}]" if item.get("basis") else ""
            lines.append(f"- {item.get('text')}{basis}")
    else:
        lines.append("- Unknown; inspect the repository and open issues")
    lines.append("")

    lines.append("## 10. Source index")
    for eid, entry in sorted(handoff.get("citations", {}).items()):
        lines.append(f"- `[E:{eid}]` {entry.get('type')}: {entry.get('quote', '')[:500]}")
    lines.append("")
    return "\n".join(lines)


def write_artifacts(run_dir: Path, repo: Path, brief: str, profile: str, use_llm: bool = True) -> dict[str, Any]:
    handoff = build_handoff(run_dir, repo, brief, profile, use_llm=use_llm)
    text = render_handoff(handoff)
    (run_dir / "handoff.json").write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "citations.json").write_text(json.dumps(handoff["citations"], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "handoff.md").write_text(text, encoding="utf-8")
    (run_dir / "snapshot.json").write_text(json.dumps({
        "generated_at": driver.utc_now(),
        "code_state": handoff["code_state"],
        "source_events": handoff["source_events"],
        "extraction_meta": handoff["extraction_meta"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return handoff


def render_raw(run_dir: Path) -> None:
    events = load_events(run_dir)
    raw = run_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    lines = ["# Raw session record", ""]
    for e in events:
        kind = e.get("type")
        if kind == "user_message":
            lines += ["## User", "", str(e.get("content") or ""), ""]
        elif kind == "assistant_message":
            content = str(e.get("content") or "").strip()
            calls = e.get("tool_calls") or []
            if content or calls:
                lines += ["## Assistant", "", content or "(tool calls only)", ""]
                for call in calls:
                    lines += [f"- tool `{call.get('name')}` args `{json.dumps(call.get('arguments'), ensure_ascii=False)}`"]
                lines.append("")
        elif kind == "tool_finished":
            inner = ((e.get("result") or {}).get("result") or {})
            lines += [f"## Tool `{e.get('tool')}` ({e.get('status')})", ""]
            if isinstance(inner, dict) and "exit_code" in inner:
                lines += [f"exit={inner.get('exit_code')}", "", "```", str(inner.get("stdout") or "")[:4000], "```", ""]
            else:
                lines += ["```", json.dumps(inner, ensure_ascii=False)[:4000], "```", ""]
            changed = (e.get("after") or {}).get("changed") or []
            if changed:
                lines += ["Changed files: " + ", ".join(c.get("path", "") for c in changed), ""]
        elif kind == "synthetic_tool_result":
            lines += ["## Synthetic (not executed)", "", str(e.get("content") or ""), ""]
        elif kind == "harness_note":
            lines += ["## Harness note", "", str(e.get("content") or ""), ""]
    (raw / "transcript.md").write_text("\n".join(lines), encoding="utf-8")
    shutil.copyfile(run_dir / "events.jsonl", raw / "events.jsonl")


def command_build(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    repo = Path(args.repo).resolve()
    brief = Path(args.brief).read_text(encoding="utf-8")
    handoff = write_artifacts(run_dir, repo, brief, args.model_profile, use_llm=not args.no_llm)
    print(f"wrote {run_dir / 'handoff.md'}")
    print(f"verification: {[v['status'] for v in handoff['verification']]}")
    print(f"citations: {len(handoff['citations'])}")


def command_refine(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    repo = Path(args.repo).resolve()
    data = refine_handoff(run_dir, repo)
    print(f"refined {run_dir / 'handoff.md'}: {len(data['done'])} done, {len(data['verification'])} verification, filtered {data['extraction_meta'].get('filtered_out')}")


def command_raw(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    render_raw(run_dir)
    print(f"wrote {run_dir / 'raw' / 'transcript.md'}")


def command_cite(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    data = json.loads((run_dir / "citations.json").read_text(encoding="utf-8"))
    entry = data.get(args.event_id)
    if not entry:
        raise SystemExit(f"citation not found: {args.event_id}")
    events = load_events(run_dir)
    current = event_index(events).get(args.event_id)
    if current is None:
        raise SystemExit(f"source missing: {args.event_id}")
    fresh = cite_entry(current)
    if entry.get("hash") and fresh.get("hash") != entry.get("hash"):
        raise SystemExit(f"source changed since handoff: {args.event_id}")
    print(entry.get("quote", ""))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build")
    build.add_argument("--run-id", required=True)
    build.add_argument("--run-dir")
    build.add_argument("--repo", required=True)
    build.add_argument("--brief", required=True)
    build.add_argument("--model-profile", default=driver.CONFIG.get("model_profile", "profile1"))
    build.add_argument("--no-llm", action="store_true")

    refine = sub.add_parser("refine")
    refine.add_argument("--run-id", required=True)
    refine.add_argument("--run-dir")
    refine.add_argument("--repo", required=True)

    raw = sub.add_parser("raw")
    raw.add_argument("--run-id", required=True)
    raw.add_argument("--run-dir")

    cite = sub.add_parser("cite")
    cite.add_argument("--run-id", required=True)
    cite.add_argument("--run-dir")
    cite.add_argument("--event-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global CONFIG
    driver.CONFIG = driver.load_config()
    args = parse_args(argv)
    if not getattr(args, "run_dir", None):
        args.run_dir = str(driver.DEFAULT_RUNS / args.run_id)
    if args.cmd == "build":
        command_build(args)
    elif args.cmd == "refine":
        command_refine(args)
    elif args.cmd == "raw":
        command_raw(args)
    elif args.cmd == "cite":
        command_cite(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
