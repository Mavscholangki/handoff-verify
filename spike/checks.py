"""Deterministic checks for the context layer (no model calls)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import context
import driver

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_events(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def check_status() -> list[str]:
    events = read_events("status.jsonl")
    by_status: dict[str, int] = {}
    for event in events:
        status = event.get("status")
        if status:
            by_status[status] = by_status.get(status, 0) + 1
    failures = []
    if by_status.get("succeeded", 0) != 2:
        failures.append(f"expected 2 succeeded, got {by_status}")
    if by_status.get("failed", 0) != 1:
        failures.append(f"expected 1 failed, got {by_status}")
    if by_status.get("not_executed", 0) != 1:
        failures.append(f"expected 1 not_executed, got {by_status}")
    if any(e.get("type") == "synthetic_tool_result" and e.get("status") == "succeeded" for e in events):
        failures.append("synthetic tool result was counted as success")
    seqs = [e.get("seq") for e in events]
    if seqs != sorted(seqs):
        failures.append("event order is not monotonic")
    return failures


def check_update() -> list[str]:
    ledger = context.Ledger()
    raw = [json.loads(line) for line in (FIXTURES / "update.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    items = {r["item_id"]: context.Item(**r) for r in raw}
    failures = []
    ledger.add(items["d1"])
    ledger.apply(items["d2"], "supersede", "d1")
    ledger.add(items["d3"])
    ledger.add(items["d4"])
    ledger.apply(context.Item("d3-x", "decision", "Use CSV delimiter semicolon", evidence=["e5"]), "withdraw", "d3")
    if ledger.items["d3"].status != "withdrawn":
        failures.append("withdraw did not take effect")
    ledger.apply(context.Item("d3-y", "decision", "Use CSV delimiter comma", evidence=["e6"]), "reopen", "d3")
    if ledger.items["d3"].status != "active":
        failures.append("reopen did not take effect")
    if ledger.items["d1"].status != "superseded":
        failures.append("old decision is not superseded")
    if ledger.items["d2"].status != "active":
        failures.append("new decision is not active")
    if len(ledger.current()) != 3:
        failures.append(f"unexpected current item count: {len(ledger.current())}")
    if ledger.items["d3"].revision < 3:
        failures.append("revision did not advance")
    return failures


def check_stale() -> list[str]:
    failures = []
    import uuid
    tmp_root = Path(__file__).resolve().parent.parent / "runs" / "_checks" / uuid.uuid4().hex[:8]
    tmp_root.mkdir(parents=True)
    try:
        repo = tmp_root / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        driver.run_git(repo, "init")
        driver.run_git(repo, "config", "user.email", "check@example.invalid")
        driver.run_git(repo, "config", "user.name", "check")
        driver.run_git(repo, "add", "-A")
        driver.run_git(repo, "commit", "-m", "init")
        tree = driver.snapshot(repo)["tree"]
        event = {
            "event_id": "evt-test-01",
            "seq": 0,
            "type": "tool_finished",
            "tool": "run_tests",
            "status": "succeeded",
            "arguments": {"command": "python -m unittest"},
            "result": {"ok": True, "result": {"exit_code": 0, "stdout": "OK"}},
            "after": {"tree": tree, "changed": []},
        }
        records = context.test_records([event], repo)
        if not records or records[0]["status"] != "passed":
            failures.append(f"test at current tree should pass: {records}")
        (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        records = context.test_records([event], repo)
        if not records or not records[0]["stale"]:
            failures.append("test after code change was not marked stale")
        if records and "app.py" not in records[0]["changed_since"]:
            failures.append(f"changed file missing: {records[0]['changed_since']}")
        # Revert to the tested state: the old evidence applies again.
        driver.run_git(repo, "checkout", "--", "app.py")
        records = context.test_records([event], repo)
        if not records or records[0]["stale"]:
            failures.append("restored tree still marked stale")
        # Delete a tracked file: the old evidence must become stale.
        (repo / "app.py").unlink()
        records = context.test_records([event], repo)
        if not records or not records[0]["stale"]:
            failures.append("deleted file was not marked stale")
        driver.run_git(repo, "checkout", "--", "app.py")
    finally:
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)
    return failures


def check_citation() -> list[str]:
    events = read_events("cite.jsonl")
    known = context.event_index(events)
    failures = []
    if context.valid_evidence(["evt-cite-01", "evt-missing"], set(known)) != ["evt-cite-01"]:
        failures.append("unknown evidence id was not filtered")
    quote = context.quote_for(known["evt-cite-01"])
    if "standard library" not in quote:
        failures.append("quote did not contain source text")
    if "exit 0" not in context.quote_for(known["evt-cite-02"]):
        failures.append("test evidence quote missing exit code")
    return failures


def main() -> int:
    checks = [
        ("status", check_status),
        ("update", check_update),
        ("stale", check_stale),
        ("citation", check_citation),
    ]
    failed = False
    for name, fn in checks:
        problems = fn()
        if problems:
            failed = True
            print(f"FAIL {name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"PASS {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
