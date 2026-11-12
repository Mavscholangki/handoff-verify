"""Create isolated C0/C1/C2 workspaces from a development run."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev-run", required=True, help="runs/<run-id> directory")
    parser.add_argument("--repo", required=True, help="development repository to copy")
    parser.add_argument("--out", required=True, help="takeover root, e.g. runs/takeover-csv")
    parser.add_argument("--conditions", default="c0,c1,c2")
    args = parser.parse_args()

    dev_dir = Path(args.dev_run).resolve()
    repo_src = Path(args.repo).resolve()
    root = Path(args.out).resolve()
    if root.exists():
        shutil.rmtree(root)

    context.render_raw(dev_dir)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    for condition in [c.strip() for c in args.conditions.split(",") if c.strip()]:
        dest = root / condition
        dest.mkdir(parents=True)
        shutil.copytree(repo_src, dest / "repo", ignore=ignore)
        if condition == "c1":
            shutil.copytree(dev_dir / "raw", dest / "raw")
        if condition == "c2":
            for name in ("handoff.md", "handoff.json", "citations.json", "snapshot.json"):
                shutil.copy2(dev_dir / name, dest / name)
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
