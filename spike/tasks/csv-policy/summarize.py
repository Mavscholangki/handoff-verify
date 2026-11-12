"""Collect CSV-policy C0/C1/C2 outcomes."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

RUNS = Path(__file__).resolve().parents[3] / "runs"
BASE = RUNS / "csv-policy"
TAKEOVER = BASE / "takeover"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def tokens_and_steps(run_dir: Path) -> tuple[int, int]:
    files = sorted((run_dir / "conversations").glob("*.jsonl"))
    if not files:
        return 0, 0
    path = files[0]
    nodes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            node = json.loads(line)
            if node.get("kind") == "node" and node.get("role") == "model":
                nodes.append(node)
    tokens = sum(int((n.get("stats") or {}).get("total_tokens") or 0) for n in nodes)
    steps = max((int((n.get("stats") or {}).get("step", -1)) for n in nodes), default=-1) + 1
    return tokens, steps


def duration(run_dir: Path) -> float:
    path = run_dir / "events.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(events) < 2:
        return 0.0
    return round((datetime.fromisoformat(events[-1]["ts"]) - datetime.fromisoformat(events[0]["ts"])).total_seconds(), 1)


rows = []
for condition in ("c0", "c1", "c2"):
    run_id = condition
    run_dir = BASE / run_id
    manifest = read_json(run_dir / "manifest.json")
    grade = read_json(run_dir / "grade.json")
    tokens, steps = tokens_and_steps(run_dir)
    material = None
    if condition == "c1":
        material = TAKEOVER / "c1" / "raw" / "transcript.md"
    elif condition == "c2":
        material = TAKEOVER / "c2" / "handoff.md"
    status = manifest.get("status", "max_steps")
    if status == "running":
        status = "max_steps (task solved)" if grade.get("passed") else "max_steps (hidden tests failed)"
    rows.append({
        "condition": condition,
        "run_id": run_id,
        "status": status,
        "tokens": tokens,
        "steps": steps,
        "duration_s": duration(run_dir),
        "hidden_tests_passed": bool(grade.get("passed")),
        "stdlib_ok": bool((grade.get("stdlib") or {}).get("ok")),
        "final_answer": bool(manifest.get("final_text")),
        "material": str(material) if material else "task brief + repo only",
        "material_chars": len(material.read_text(encoding="utf-8")) if material and material.is_file() else 0,
    })

summary = {"rows": rows}
(BASE / "value_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
for row in rows:
    (BASE / row["run_id"] / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    print(row)
