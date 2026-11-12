# Spike plan (1.5 days)

## Goal

Quickly test whether a Harness-style recorder + context ledger + deterministic
handoff can support a fresh agent taking over a small development task, and
identify the minimum event contract the final Harness must expose.

This is a feasibility spike for a course project, not a product acceptance
test.  The reference harness (`D:\Projects\NewHarness`) is used as a test tool
only.

## Scope

- One real development task (`parse_pagination` first, `export_csv` left over).
- One interruption (kill during `sleep`) and one resume.
- One requirement change (422 -> 400) and one external edit after the last test.
- One LLM extraction; handoff rendered deterministically from the ledger.
- C0/C1/C2 takeover: task+repo only / full raw record / auto handoff.
- Deterministic fixtures for status, update, stale and citation cases.

Out of scope: multiple tasks/models, independent user study, performance,
security review, full Harness implementation, GUI.

## Success criteria used

- No missing critical events; failed/in-flight/unknown not reported as success.
- A restarted session recovers the persisted state and reports what is missing.
- Paraphrase/withdraw/reopen updates keep one current state and history.
- A test result after code changes is marked stale; no false "current" result.
- Handoff has the required sections and citations; no unsupported "done/passed".
- A fresh agent can pass hidden tests; C2 is compared with C1 and C0 on tokens,
  steps and wall time.

## Key decisions

- Use NewHarness for the agent loop, tool registry, conversation store and
  model adapter; do not modify it.
- Add only `run_tests`, `sleep`, an `execute` wrapper for git snapshots/events,
  and the context layer.
- Keep the model profile configurable in `spike/config.toml`; no hard-coded
  profile in code.
- Use a deterministic handoff template after one extraction call; filter
  routine reads and extraction artifacts rather than paying for another call.
- Treat all data as synthetic; do not use real project secrets.

## Artifacts

See `report.md` and the `runs/` directory.  `checks.py` and `grade.py` are
reusable against a later Harness implementation.

A second task (`tasks/csv-policy/`) was added after the first run because the
first task did not separate C0 from C2: its remaining work was inferable from
the repository.  The second task puts the needed product decisions only in the
recorded development brief/conversation; see `report-csv-policy.md`.
