import csv
import io
import unittest

from app import ValidationError, export_csv, parse_pagination


class PaginationAcceptance(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(parse_pagination({}), {"limit": 20, "offset": 0})

    def test_string_values(self) -> None:
        self.assertEqual(
            parse_pagination({"limit": "50", "offset": "10"}),
            {"limit": 50, "offset": 10},
        )

    def test_limit_bounds(self) -> None:
        for value in ("-1", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as ctx:
                    parse_pagination({"limit": value})
                self.assertEqual(ctx.exception.code, 400)

    def test_limit_is_capped(self) -> None:
        self.assertEqual(parse_pagination({"limit": "250"}), {"limit": 100, "offset": 0})

    def test_offset_bounds(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            parse_pagination({"offset": "-1"})
        self.assertEqual(ctx.exception.code, 400)


class CsvAcceptance(unittest.TestCase):
    def test_header_and_rows(self) -> None:
        text = export_csv([
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "25"},
        ])
        self.assertTrue(text.endswith("\n"))
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows, [
            ["name", "age"],
            ["Alice", "30"],
            ["Bob", "25"],
        ])

    def test_empty(self) -> None:
        self.assertEqual(export_csv([]), "")

    def test_quotes(self) -> None:
        text = export_csv([{"name": "A, B", "note": 'say "hi"'}])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[1], ["A, B", 'say "hi"'])


if __name__ == "__main__":
    unittest.main()
