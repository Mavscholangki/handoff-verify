import csv
import io
import unittest

from app import export_csv


class CsvPolicyAcceptance(unittest.TestCase):
    def test_empty_input(self) -> None:
        self.assertEqual(export_csv([]), "")

    def test_lf_only(self) -> None:
        text = export_csv([{"a": 1, "b": 2}])
        self.assertNotIn("\r", text)
        self.assertEqual(text, "a,b\n1,2\n")

    def test_booleans_are_lowercase(self) -> None:
        text = export_csv([{"active": True, "deleted": False}])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows, [["active", "deleted"], ["true", "false"]])

    def test_missing_and_extra_keys(self) -> None:
        text = export_csv([
            {"a": 1, "b": 2},
            {"a": 3, "c": 4},
            {"b": "z"},
        ])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows, [
            ["a", "b"],
            ["1", "2"],
            ["3", ""],
            ["", "z"],
        ])

    def test_none_is_empty(self) -> None:
        text = export_csv([{"a": None, "b": 0}])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows, [["a", "b"], ["", "0"]])

    def test_all_empty_rows_are_skipped(self) -> None:
        text = export_csv([
            {"a": "", "b": ""},
            {"a": "x", "b": "y"},
            {"a": None, "b": ""},
        ])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows, [["a", "b"], ["x", "y"]])
        self.assertNotIn("\n\n", text)

    def test_header_only_when_all_rows_empty(self) -> None:
        text = export_csv([{"a": ""}, {"a": ""}])
        self.assertEqual(text, "a\n")

    def test_quoting_and_order(self) -> None:
        text = export_csv([
            {"b": 'x, "y"', "a": "line1\nline2"},
            {"b": "z", "a": "w"},
        ])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["b", "a"])
        self.assertEqual(rows[1], ['x, "y"', "line1\nline2"])
        self.assertEqual(rows[2], ["z", "w"])


if __name__ == "__main__":
    unittest.main()
