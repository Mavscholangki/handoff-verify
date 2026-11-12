"""Create isolated C0/C1/C2 workspaces from the dev run."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import context
import driver

dev_dir = driver.DEFAULT_RUNS / "dev-01"
repo_src = Path(r"D:\Projects\handoff-verify\spike\task\repo")
root = driver.DEFAULT_RUNS / "takeover"

if root.exists():
    shutil.rmtree(root)

# Refresh the raw rendering before copying.
context.render_raw(dev_dir)

ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
for condition in ("c0", "c1", "c2"):
    dest = root / condition
    (dest / "repo").parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo_src, dest / "repo", ignore=ignore)
    if condition == "c1":
        shutil.copytree(dev_dir / "raw", dest / "raw")
    if condition == "c2":
        for name in ("handoff.md", "handoff.json", "citations.json", "snapshot.json"):
            shutil.copy2(dev_dir / name, dest / name)
print(root)
