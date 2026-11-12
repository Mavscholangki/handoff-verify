# Handoff context spike

A feasibility spike for automatically recording a development session,
extracting a context ledger, and producing a handoff that a fresh agent can
use.  The reference harness `D:\Projects\NewHarness` is used only as a test
tool; it is not the product.

Start with:

- [docs/plan.md](docs/plan.md) — scope and decisions
- [docs/report.md](docs/report.md) — first task: results and Harness event-contract advice
- [docs/report-csv-policy.md](docs/report-csv-policy.md) — second task: a task that actually separates C0/C1/C2
- [runs/pagination-csv/dev-01/handoff.md](runs/pagination-csv/dev-01/handoff.md) — first-task handoff
- [runs/csv-policy/dev-01/handoff.md](runs/csv-policy/dev-01/handoff.md) — second-task handoff
- [runs/pagination-csv/value_summary.json](runs/pagination-csv/value_summary.json) — first-task comparison
- [runs/csv-policy/value_summary.json](runs/csv-policy/value_summary.json) — second-task comparison

## Layout

```
spike/
  driver.py              NewHarness driver + git snapshots + run_tests/sleep
  context.py             event normalization, ledger, extraction, handoff, citations
  checks.py              deterministic fixtures (status/update/stale/citation)
  grade.py               hidden tests + stdlib-only check
  prepare_takeover.py    build isolated C0/C1/C2 workspaces
  summarize.py           first-task takeover metrics
  repair_run.py          normalize an interrupted run
  tasks/
    README.md            task index
    pagination-csv/      first task (repo, visible/hidden tests, brief)
    csv-policy/          second task (non-obvious policy, dev/takeover briefs)
      make_handoff.py    deterministic handoff from explicit decisions
      summarize.py       second-task takeover metrics
runs/
  pagination-csv/
    dev-01/              real session, handoff, citations, recovery, claim review
    c0/ c1/ c2/          takeover run logs, results, grade.json
    takeover/            isolated takeover workspaces
    value_summary.json
  csv-policy/
    dev-01/              real session, rule-based handoff
    c0/ c1/ c2/          takeover run logs, results, grade.json
    takeover/            isolated takeover workspaces
    value_summary.json
docs/
```

## Rerun

Use the NewHarness virtualenv so its dependencies and model profiles are
available.  The profile is read from `spike/config.toml` and can be overridden
with `HANDOFF_MODEL_PROFILE` or `--model-profile`.

First task (pagination + CSV):

```powershell
$py = 'D:\Projects\NewHarness\.venv\Scripts\python.exe'
& $py spike\checks.py

& $py spike\driver.py dev --run-id dev-02 `
  --run-dir runs\pagination-csv\dev-02 `
  --repo spike\tasks\pagination-csv\repo `
  --brief spike\tasks\pagination-csv\brief.md `
  --script spike\tasks\pagination-csv\prompts\dev_prompts.json

& $py spike\context.py build --run-id dev-02 --run-dir runs\pagination-csv\dev-02 `
  --repo spike\tasks\pagination-csv\repo --brief spike\tasks\pagination-csv\brief.md

& $py spike\context.py refine --run-id dev-02 --run-dir runs\pagination-csv\dev-02 --repo spike\tasks\pagination-csv\repo
& $py spike\context.py raw --run-id dev-02 --run-dir runs\pagination-csv\dev-02
```

Second task (CSV policy):

```powershell
& $py spike\driver.py dev --run-id csv-dev-02 `
  --run-dir runs\csv-policy\dev-02 `
  --repo spike\tasks\csv-policy\repo `
  --brief spike\tasks\csv-policy\dev_brief.md `
  --script spike\tasks\csv-policy\prompts\dev.json

& $py spike\tasks\csv-policy\make_handoff.py `
  --run-id csv-dev-02 --run-dir runs\csv-policy\dev-02 `
  --repo spike\tasks\csv-policy\repo `
  --brief spike\tasks\csv-policy\dev_brief.md

& $py spike\prepare_takeover.py `
  --dev-run runs\csv-policy\dev-01 `
  --repo spike\tasks\csv-policy\repo `
  --out runs\csv-policy\takeover

& $py spike\driver.py takeover --run-id c2 `
  --run-dir runs\csv-policy\c2 `
  --workspace runs\csv-policy\takeover\c2 `
  --repo runs\csv-policy\takeover\c2\repo `
  --brief spike\tasks\csv-policy\brief.md `
  --condition c2 `
  --handoff runs\csv-policy\takeover\c2\handoff.md `
  --citations runs\csv-policy\takeover\c2\citations.json

& $py spike\grade.py --repo runs\csv-policy\takeover\c2\repo `
  --hidden spike\tasks\csv-policy\hidden --functions export_csv
```

The committed `runs/` are the actual artifacts; rerunning will spend model
quota.  For a smoke test without the model, use
`spike\driver.py dev --fake-smoke`.
