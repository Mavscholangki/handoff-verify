"""Normalize seq numbers and rebuild a manifest from the event log.

The first interrupted run exposed two small driver bugs (seq reset on resume,
manifest overwrite on resume).  The code is fixed; this utility repairs the
already-produced run without touching its event ids.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import driver

run_dir = Path(sys.argv[1]).resolve()
repo = Path(sys.argv[2]).resolve()
events = [
    json.loads(line)
    for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
for index, event in enumerate(events):
    event["seq"] = index
(run_dir / "events.jsonl").write_text(
    "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
    encoding="utf-8",
)

turns = [e for e in events if e.get("type") == "turn_finished"]
prompts = [e.get("content", "") for e in events if e.get("type") == "user_message"]
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
manifest["status"] = "done"
manifest["prompts"] = prompts
manifest["turns"] = [
    {
        "prompt": t.get("prompt"),
        "final_text": t.get("final_text"),
        "steps": t.get("steps"),
        "tokens": t.get("tokens"),
        "usage": t.get("usage"),
        "duration_s": t.get("duration_s"),
    }
    for t in turns
]
manifest["total_tokens"] = sum(int(t.get("tokens") or 0) for t in turns)
manifest["final_tree"] = driver.snapshot(repo)["tree"]
manifest["finished_at"] = events[-1].get("ts") if events else None
(run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"repaired {run_dir}: {len(events)} events, {len(turns)} finished turns, {manifest['total_tokens']} tokens")
