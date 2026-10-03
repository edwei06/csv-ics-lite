"""Check calendar serialization, strict inputs, and non-destructive CLI behavior."""
from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from csv_ics_lite.cli import main
from csv_ics_lite.core import CalendarError, convert, event_uid, fold_line, parse_events

STAMP = "2026-10-04T00:00:00Z"


def csv_text(rows, headers=("id", "summary", "start", "end", "description", "location")):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue()


def event(identifier="a", summary="Review", start="2026-10-04", end="2026-10-05",
          description="", location=""):
    return [identifier, summary, start, end, description, location]


class CalendarTests(unittest.TestCase):
    def test_all_day_has_date_values_and_exclusive_end(self):
        data, count = convert(csv_text([event()]), stamp=STAMP)
        self.assertEqual(count, 1)
        self.assertIn(b"DTSTART;VALUE=DATE:20261004\r\n", data)
        self.assertIn(b"DTEND;VALUE=DATE:20261005\r\n", data)
        self.assertIn(b"DTSTAMP:20261004T000000Z\r\n", data)
        self.assertIn(b"UID:", data)
        self.assertTrue(data.endswith(b"END:VCALENDAR\r\n"))

    def test_explicit_offset_is_normalized_to_utc_across_date_boundary(self):
        row = event(start="2026-10-04T01:30:00+08:00", end="2026-10-04T02:30:00+08:00")
        data, _ = convert(csv_text([row]), stamp=STAMP)
        self.assertIn(b"DTSTART:20261003T173000Z\r\n", data)
        self.assertIn(b"DTEND:20261003T183000Z\r\n", data)

    def test_inclusive_date_mode_adds_one_day(self):
        row = event(start="2026-12-31", end="2026-12-31")
        data, _ = convert(csv_text([row]), stamp=STAMP, inclusive_all_day_end=True)
        self.assertIn(b"DTEND;VALUE=DATE:20270101\r\n", data)

    def test_naive_unknown_offset_fractional_and_leap_second_rejected(self):
        for start in ["2026-10-04T10:00:00", "2026-10-04T10:00:00-00:00",
                      "2026-10-04T10:00:00.1Z", "2026-10-04T10:00:60Z", "2026-10-04T10:00:00+01:60"]:
            with self.assertRaises(CalendarError, msg=start):
                convert(csv_text([event(start=start, end="2026-10-04T11:00:00Z")]))

    def test_mixed_date_and_datetime_rejected(self):
        with self.assertRaises(CalendarError):
            parse_events(csv_text([event(end="2026-10-05T00:00:00Z")]))

    def test_end_must_follow_start_in_actual_utc_time(self):
        for row in [event(end="2026-10-04"),
                    event(start="2026-10-04T10:00:00Z", end="2026-10-04T11:00:00+08:00")]:
            with self.assertRaises(CalendarError):
                parse_events(csv_text([row]))

    def test_invalid_dates_and_upper_boundary_fail(self):
        for row in [event(start="2026-02-30"), event(start="9999-12-31", end="9999-12-31")]:
            with self.assertRaises(CalendarError):
                parse_events(csv_text([row]), inclusive_all_day_end=True)

    def test_text_escaping_preserves_newlines_and_prevents_property_injection(self):
        row = event(summary="Review, phase; one\\two",
                    description="First\r\nEND:VEVENT\nBEGIN:VALARM", location="Room: 1")
        data, _ = convert(csv_text([row]), stamp=STAMP)
        self.assertIn(b"SUMMARY:Review\\, phase\\; one\\\\two\r\n", data)
        self.assertIn(b"DESCRIPTION:First\\nEND:VEVENT\\nBEGIN:VALARM\r\n", data)
        self.assertIn(b"LOCATION:Room: 1\r\n", data)
        self.assertEqual(data.count(b"\r\nEND:VEVENT\r\n"), 1)

    def test_utf8_folding_never_splits_characters_or_exceeds_75_octets(self):
        logical = "SUMMARY:" + "中文🙂" * 70
        physical = fold_line(logical).encode("utf-8").split(b"\r\n")
        for line in physical:
            self.assertLessEqual(len(line), 75)
            line.decode("utf-8")
        unfolded = b"".join([physical[0], *[line[1:] for line in physical[1:]]]).decode("utf-8")
        self.assertEqual(unfolded, logical)

    def test_uid_stable_with_edits_and_namespace_scoped(self):
        first, _ = convert(csv_text([event(summary="Old")]), stamp=STAMP)
        second, _ = convert(csv_text([event(summary="New")]), stamp=STAMP)
        uid1 = next(line for line in first.split(b"\r\n") if line.startswith(b"UID:"))
        uid2 = next(line for line in second.split(b"\r\n") if line.startswith(b"UID:"))
        self.assertEqual(uid1, uid2)
        self.assertNotEqual(event_uid("alpha", "a"), event_uid("beta", "a"))
        self.assertNotEqual(event_uid("ab", "c"), event_uid("a", "bc"))

    def test_duplicate_ids_rejected_without_echoing_id(self):
        with self.assertRaises(CalendarError) as caught:
            parse_events(csv_text([event("private-id"), event("private-id")]))
        self.assertNotIn("private-id", str(caught.exception))

    def test_required_duplicate_unknown_and_blank_headers(self):
        for headers in [("summary", "start", "end"), ("id", "id", "start", "end"),
                        ("id", "summary", "start", "end", "typo"), ("id", "", "start", "end")]:
            with self.assertRaises(CalendarError):
                parse_events(csv_text([], headers=headers))

    def test_header_order_is_irrelevant_optional_fields_can_be_omitted(self):
        text = csv_text([["2026-10-05", "Review", "a", "2026-10-04"]],
                        headers=("end", "summary", "id", "start"))
        data, _ = convert(text, stamp=STAMP)
        self.assertIn(b"SUMMARY:Review", data)
        self.assertNotIn(b"DESCRIPTION:", data)

    def test_bom_and_multiline_csv_fields(self):
        data, _ = convert("\ufeff" + csv_text([event(description="one\ntwo")]), stamp=STAMP)
        self.assertIn(b"DESCRIPTION:one\\ntwo", data)

    def test_empty_data_blank_rows_and_wrong_width_fail(self):
        for text in ["", "id,summary,start,end\n", csv_text([event()]) + "\n",
                     "id,summary,start,end\na,Review,2026-10-04\n"]:
            with self.assertRaises(CalendarError):
                parse_events(text)

    def test_empty_summary_invalid_id_and_control_characters_fail(self):
        for row in [event(summary=" "), event(identifier=" "), event(identifier="a b"),
                    event(description="bad__CONTROL__value"), event(summary="bad\x7fvalue")]:
            with self.assertRaises(CalendarError):
                parse_events(csv_text([row]).replace("__CONTROL__", "\x00"))

    def test_malformed_quoting_and_event_limit_fail(self):
        with self.assertRaises(CalendarError):
            parse_events('id,summary,start,end\na,"unclosed,2026-10-04,2026-10-05\n')
        with self.assertRaisesRegex(CalendarError, "limit"):
            parse_events(csv_text([event("a"), event("b")]), max_events=1)

    def test_fixed_stamp_is_reproducible_and_namespace_validated(self):
        text = csv_text([event()])
        self.assertEqual(convert(text, stamp=STAMP), convert(text, stamp=STAMP))
        for options in [{"stamp": "2026-10-04"}, {"stamp": ""}, {"namespace": ""}, {"namespace": "bad\nname"}]:
            with self.assertRaises(CalendarError):
                convert(text, **options)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "events.csv"
        self.source.write_bytes(csv_text([event()]).encode("utf-8"))
        self.target = self.root / "events.ics"

    def call(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main([str(self.source), *[str(arg) for arg in args]])
        return status, output.getvalue(), errors.getvalue()

    def test_check_json_creates_no_file(self):
        status, output, _ = self.call("--check", "--format", "json")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), {"ok": True, "events": 1, "written": False})
        self.assertEqual([path.name for path in self.root.iterdir()], ["events.csv"])

    def test_output_and_force_preserve_source(self):
        original = self.source.read_bytes()
        self.assertEqual(self.call(self.target, "--stamp", STAMP)[0], 0)
        first = self.target.read_bytes()
        self.assertEqual(self.call(self.target)[0], 2)
        self.assertEqual(self.target.read_bytes(), first)
        self.assertEqual(self.call(self.target, "--force", "--stamp", STAMP)[0], 0)
        self.assertEqual(self.target.read_bytes(), first)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(list(self.root.glob(".csv-ics-lite-*.tmp")))

    def test_same_source_target_is_always_refused(self):
        original = self.source.read_bytes()
        self.assertEqual(self.call(self.source, "--force")[0], 2)
        self.assertEqual(self.source.read_bytes(), original)

    def test_invalid_data_does_not_create_or_replace_output(self):
        self.source.write_text("broken", encoding="utf-8")
        self.assertEqual(self.call(self.target)[0], 1)
        self.assertFalse(self.target.exists())
        self.target.write_bytes(b"existing calendar")
        self.assertEqual(self.call(self.target, "--force")[0], 1)
        self.assertEqual(self.target.read_bytes(), b"existing calendar")

    def test_byte_limits_encoding_and_missing_inputs(self):
        self.assertEqual(self.call("--check", "--max-bytes", "2")[0], 1)
        self.source.write_bytes(b"\xff")
        self.assertEqual(self.call("--check")[0], 1)
        self.source.unlink()
        self.assertEqual(self.call("--check")[0], 2)


if __name__ == "__main__":
    unittest.main()
