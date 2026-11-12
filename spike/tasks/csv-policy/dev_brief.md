# CSV export — development brief

Implement `export_csv(rows)` in `app.py` using only the Python standard
library.  The function returns CSV text with a header row taken from the
first row's keys, in that order.

Product decisions recorded for the downstream consumer:

- Line terminator must be LF (`\n`) only.  The downstream splitter splits on
  `\n`; CRLF must not appear.
- Booleans serialize as lowercase `true` / `false`, not Python `True` /
  `False`.
- `None` and a missing key become an empty field.
- Keys in a later row that are not in the header are ignored.
- A row whose values are all empty after conversion is skipped (it must not
  produce a blank line).
- An empty input list returns the empty string.

Development order for this phase:

- Implement the happy path now: header from the first row, string values for
  ordinary rows, LF line endings, and quoting through the `csv` module.
- Leave the boolean/None/missing-key/extra-key/all-empty-row policy for the
  next person; the decisions above are recorded for that handoff.
- Run the visible tests when done.
