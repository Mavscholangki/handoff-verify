"""Run hidden acceptance tests and a small stdlib-only constraint check."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent
HIDDEN = SPIKE_DIR / "task" / "hidden"


def stdlib_check(repo: Path) -> dict:
    app = repo / "app.py"
    try:
        tree = ast.parse(app.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in allowed:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root not in allowed:
                bad.append(node.module)
    funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return {
        "ok": not bad and {"parse_pagination", "export_csv"} <= funcs,
        "bad_imports": bad,
        "has_functions": sorted({"parse_pagination", "export_csv"} & funcs),
    }


def run(repo: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo) + os.pathsep + str(HIDDEN) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(HIDDEN), "-p", "test_*.py", "-v"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    return {
        "passed": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-4000:],
        "stdlib": stdlib_check(repo),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = run(Path(args.repo).resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["passed"] and result["stdlib"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
