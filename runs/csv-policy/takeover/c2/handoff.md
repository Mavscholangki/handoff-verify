# Handoff: csv-dev-01

- Generated: 2026-11-12T13:31:15.259468+00:00
- Code tree: `0b7c73c5f292d2ead31dc7f535eefdc7f8d5c91f`
- Source events: 33

## 1. Task goal
Implement `export_csv(rows)` in `app.py` using only the Python standard library.  The function returns CSV text with a header row taken from the first row's keys, in that order. [E:evt-abeb75d693]

## 2. Current state
- Worktree: dirty at tree `0b7c73c5f292d2ead31dc7f535eefdc7f8d5c91f`
- Remaining: export_csv does not handle empty input; test_empty_list fails with IndexError.
- Latest verification: failed (exit 1 at tree `0b7c73c5f292d2ead31dc7f535eefdc7f8d5c91f`)

## 3. Constraints
- Line terminator must be LF (`\n`) only.  The downstream splitter splits on `\n`; CRLF must not appear. [E:evt-abeb75d693]
- Booleans serialize as lowercase `true` / `false`, not Python `True` / `False`. [E:evt-abeb75d693]
- `None` and a missing key become an empty field. [E:evt-abeb75d693]
- Keys in a later row that are not in the header are ignored. [E:evt-abeb75d693]
- A row whose values are all empty after conversion is skipped (it must not produce a blank line). [E:evt-abeb75d693]
- An empty input list returns the empty string. [E:evt-abeb75d693]

## 4. Completed
- Implemented happy-path export_csv (header from first row, str values, LF, csv quoting). [E:evt-3249b17ef5]
- Ran the visible tests: failed (exit 1); the empty-list test fails. [E:evt-98dd388759]

## 5. Key decisions
- **Use LF line endings; the downstream splitter splits on LF.** — reason: Recorded downstream consumer decision. [E:evt-abeb75d693]
- **Apply the deferred policy: booleans lowercase, None/missing empty, extra keys ignored, all-empty rows skipped.** — reason: Recorded product decisions for the downstream consumer. [E:evt-abeb75d693]
- **Implement the happy path first; leave the edge policy for the next person.** — reason: Explicit development order in the brief. [E:evt-abeb75d693] [E:evt-198a6c7627]

## 6. Code changes
- `app.py` ( M) [E:evt-d3ba345d86]
- Worktree diff: dirty
```
app.py | 23 +++++++++++++++++++++--
 1 file changed, 21 insertions(+), 2 deletions(-)
```

## 7. Verification
- FAILED: `python -m unittest discover -s tests -q` exit=1 at tree `0b7c73c5f292d2ead31dc7f535eefdc7f8d5c91f` [E:evt-98dd388759]
  - output: `======================================================================
ERROR: test_empty_list (test_export.ExportTests.test_empty_list)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "D:\Projects\handoff-verify\spike\tasks\csv-policy\`

## 8. Open issues and unknowns
- export_csv does not handle empty input; test_empty_list fails with IndexError. [E:evt-98dd388759]
- Deferred policy is not implemented: booleans, None/missing keys, extra keys, all-empty rows. [E:evt-abeb75d693]
- The visible test suite currently fails; the next session must leave it green. [E:evt-98dd388759]
- The downstream consumer itself is not in this repository; the policy is taken from the recorded brief.

## 9. Suggested next steps
- Add an empty-list guard returning an empty string. [basis: evt-98dd388759]
- Implement the deferred policy and rerun the visible tests. [basis: evt-abeb75d693]
- Verify output has no CRLF, booleans are lowercase, and all-empty rows are skipped. [basis: evt-abeb75d693]

## 10. Source index
- `[E:evt-198a6c7627]` user_message: Read TASK.md and implement export_csv in app.py. Work only on the happy path: header from the first row's keys in order, ordinary values converted with str(), LF line endings via csv.writer(lineterminator='\n'), and csv-module quoting. The deferred product policy is: booleans serialize lowercase true/false; None and missing keys become empty fields; extra keys in later rows are ignored; a row whose values are all empty is skipped; an empty list returns the empty string. Do NOT implement these de
- `[E:evt-3249b17ef5]` tool_finished: edit_file -> succeeded replaced 1 occurrence(s) in app.py (line 14) changed=app.py
- `[E:evt-98dd388759]` tool_finished: run_tests -> failed (exit 1) ====================================================================== ERROR: test_empty_list (test_export.ExportTests.test_empty_list) ---------------------------------------------------------------------- Traceback (most recent call last): File "D:\Projects\handoff-verify\spike\tasks\csv-policy\ changed=app.py
- `[E:evt-abeb75d693]` task_brief: # CSV export — development brief Implement `export_csv(rows)` in `app.py` using only the Python standard library. The function returns CSV text with a header row taken from the first row's keys, in that order. Product decisions recorded for the downstream consumer: - Line terminator must be LF (`\n`) only. The downstream splitter splits on `\n`; CRLF must not appear. - Booleans serialize as lowercase `true` / `false`, not Python `True` / `False`. - `None` and a missing key become an empty field.
- `[E:evt-d3ba345d86]` tool_finished: run_tests -> succeeded (exit 0) 'name,age\nAda,36\nGrace,45\n' changed=app.py
