# Task brief

Implement two small helpers in `repo/app.py`:

1. `parse_pagination(params)` — read `limit` and `offset` from a query dict.
   - default limit: 20
   - maximum limit: 100
   - default offset: 0
   - negative or non-integer values raise `ValidationError`
2. `export_csv(rows)` — return CSV text with a header row.
   - header comes from the first row's keys, in that order
   - comma separated, newline terminated
   - use the standard library `csv` module
   - an empty list returns an empty string

Constraints:

- standard library only; do not add dependencies
- keep the public function names and signatures
- run tests with `python -m unittest discover -s tests -q`

Development order:

- First implement only `parse_pagination`.
- Leave `export_csv` for the next person.
- The validation error code starts at 422.  The product owner may change it
  to 400 during the session; follow the latest instruction.
