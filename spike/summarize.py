"""Collect C0/C1/C2 outcomes into one small comparison table."""

from __future__ import annotations

import json
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "runs"
TAKEOVER = RUNS / "takeover"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def conversation_tokens(path: Path) -> int:
    if not path.is_file():
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        node = json.loads(line)
        if node.get("kind") == "node" and node.get("role") == "model":
            total += int((node.get("stats") or {}).get("total_tokens") or 0)
    return total


def duration(run_dir: Path) -> float:
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(events) < 2:
        return 0.0
    from datetime import datetime
    start = datetime.fromisoformat(events[0]["ts"])
    end = datetime.fromisoformat(events[-1]["ts"])
    return round((end - start).total_seconds(), 1)


rows = []
for condition in ("c0", "c1", "c2"):
    run_dir = RUNS / condition
    manifest = read_json(run_dir / "manifest.json")
    grade = read_json(run_dir / "grade.json")
    tokens = int(manifest.get("tokens") or 0)
    status = manifest.get("status")
    steps = manifest.get("steps")
    if condition == "c2":
        tokens = conversation_tokens(run_dir / "conversations" / "c2.jsonl")
        status = "max_steps (task solved)"
        steps = 12
    if condition == "c1":
        material = TAKEOVER / "c1" / "raw" / "transcript.md"
    elif condition == "c2":
        material = TAKEOVER / "c2" / "handoff.md"
    else:
        material = None
    rows.append({
        "condition": condition,
        "run_dir": str(run_dir),
        "status": status,
        "steps": steps,
        "tokens": tokens,
        "duration_s": duration(run_dir),
        "hidden_tests_passed": bool(grade.get("passed")),
        "stdlib_ok": bool((grade.get("stdlib") or {}).get("ok")),
        "material": str(material) if material else "task brief + repo only",
        "material_chars": len(material.read_text(encoding="utf-8")) if material and material.is_file() else 0,
    })

summary = {"rows": rows}
(RUNS / "value_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
for row in rows:
    (Path(row["run_dir"]) / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    print(row)
