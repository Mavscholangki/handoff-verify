# Task fixtures

Each task is self-contained: `repo/` is the working repository, `hidden/` holds
acceptance tests that are not visible to the developer agent, and `brief.md`
is what the takeover session receives.

| Task | Purpose | Result |
|---|---|---|
| `pagination-csv/` | First task: pagination + CSV, mostly inferable from the repo and visible tests. | C0/C1/C2 all pass; handoff value is not demonstrated. |
| `csv-policy/` | Second task: the remaining work depends on non-obvious product decisions recorded only in the development brief/conversation. | C0 fails 2/8 hidden tests; C1/C2 pass; C2 is fastest. |

`csv-policy/` has two briefs:

- `dev_brief.md` — full product decisions, used by the development session and
  recorded as the `task_brief` event;
- `brief.md` — takeover brief without the decisions, so C0 must infer them.

The handoff for `csv-policy` is built by `make_handoff.py` from the explicit
decision bullets in the recorded brief.  This is deterministic and uses no
model calls; the LLM extraction path had timed out twice on this small task.

The checked-in working trees are the handoff snapshots from the recorded dev
runs, not clean starting points.  To replay a task from its initial commit:

```powershell
git -C spike\tasks\<task>\repo checkout -- .
git -C spike\tasks\<task>\repo clean -fd
```

