# Interruption / recovery report

The dev session was killed while the `sleep` tool was running (`kill-on-sleep`).

What the event log preserves:

- Two completed turns (initial implementation; 422 -> 400 change).
- A third turn that started: `edit_file`, `run_tests`, another `run_tests`,
  then `tool_started sleep` (`evt-3370c61d31`).
- No `tool_finished` for that sleep call: it is the in-flight tool.
- No `turn_finished` for the third turn (the process died before the turn was
  persisted by NewHarness).
- `session_resumed` after restart, followed by a fresh inspection and test run.

What is preserved vs missing:

| Item | Status |
|---|---|
| File edits before the kill | Preserved on disk and in the tool events |
| Test results before the kill | Preserved as tool events with tree hashes |
| Third turn's conversation messages | Missing from NewHarness conversation history (turn not persisted) |
| Sleep tool outcome | Unknown: started, never finished |
| Code after resume | Inspected and tested again; current tree is `142f0b56...` |

The handoff therefore says the previous session was interrupted and resumed,
and labels the old test results stale.  It does not claim the sleep call
succeeded.
