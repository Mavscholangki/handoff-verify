# Handoff context spike

A 1.5-day feasibility spike for automatically recording a development session,
extracting a context ledger, and producing a handoff that a fresh agent can
use.  The reference harness `D:\Projects\NewHarness` is used only as a test
tool; it is not the product.

Start with:

- [docs/plan.md](docs/plan.md) — scope and decisions
- [docs/report.md](docs/report.md) — results and Harness event-contract advice
- [runs/dev-01/handoff.md](runs/dev-01/handoff.md) — generated handoff
- [runs/value_summary.json](runs/value_summary.json) — C0/C1/C2 comparison

## Layout

```
spike/
  driver.py            NewHarness driver + git snapshots + run_tests/sleep
  context.py           event normalization, ledger, extraction, handoff, citations
  checks.py            deterministic fixtures (status/update/stale/citation)
  grade.py             hidden tests + stdlib-only check
  prepare_takeover.py  build isolated C0/C1/C2 workspaces
  summarize.py         collect takeover metrics
  task/                small repo, visible tests, hidden tests
  config.toml          model profile and timeouts
  scripts/             dev and resume prompts
runs/
  dev-01/              real session, handoff, citations, recovery, claim review
  takeover/            C0/C1/C2 workspaces
  c0/ c1/ c2/          takeover run logs, manifests/results, grade.json
docs/
```

## Rerun

Use the NewHarness virtualenv so its dependencies and model profiles are
available.  The profile is read from `spike/config.toml` and can be overridden
with `HANDOFF_MODEL_PROFILE` or `--model-profile`.

```powershell
$py = 'D:\Projects\NewHarness\.venv\Scripts\python.exe'

& $py spike\checks.py

# A fresh dev run needs a clean task repo and a new run id.
& $py spike\driver.py dev --run-id dev-02 `
  --repo spike\task\repo `
  --brief spike\task\brief.md `
  --script spike\scripts\dev_prompts.json

& $py spike\context.py build --run-id dev-02 `
  --repo spike\task\repo --brief spike\task\brief.md

& $py spike\context.py refine --run-id dev-02 --repo spike\task\repo
& $py spike\context.py raw --run-id dev-02
```

The committed `runs/` are the actual artifacts from the first run; rerunning
will spend model quota.  For a smoke test without the model, use
`spike\driver.py dev --fake-smoke`.
