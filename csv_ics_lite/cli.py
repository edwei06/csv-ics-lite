"""Validate before creating or replacing a calendar output."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from . import __version__
from .core import CalendarError, convert


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def write_calendar(output, data, *, force=False):
    path = Path(output)
    if not force:
        # Exclusive creation prevents silently replacing an existing file.
        stream = path.open("xb")
        try:
            with stream:
                stream.write(data)
                stream.flush()
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".csv-ics-lite-", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert event CSV to an offline, UTF-8 iCalendar file.")
    parser.add_argument("input", help="UTF-8 CSV path")
    parser.add_argument("output", nargs="?", help="New ICS path")
    parser.add_argument("--check", action="store_true", help="Validate without writing a calendar")
    parser.add_argument("--force", action="store_true", help="Replace an existing output after validation")
    parser.add_argument("--namespace", default="default", help="Stable event UID namespace")
    parser.add_argument("--stamp", help="Explicit offset timestamp for reproducible DTSTAMP")
    parser.add_argument("--inclusive-all-day-end", action="store_true",
                        help="Treat date-only end values as inclusive and add one day")
    parser.add_argument("--max-events", type=positive, default=10000)
    parser.add_argument("--max-bytes", type=positive, default=8 * 1024 * 1024)
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Validation summary format")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if args.check and args.output:
        parser.error("--check does not accept an output path")
    if not args.check and not args.output:
        parser.error("An output path is required unless --check is used")
    source = Path(args.input)
    destination = Path(args.output) if args.output else None
    try:
        if destination is not None:
            if source.resolve() == destination.resolve() or (destination.exists() and os.path.samefile(source, destination)):
                raise OSError("Input and output must be different files")
        with source.open("rb") as stream:
            raw = stream.read(args.max_bytes + 1)
        if len(raw) > args.max_bytes:
            raise CalendarError("Input byte limit exceeded")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as error:
            raise CalendarError("CSV input must be UTF-8") from error
        data, count = convert(text, namespace=args.namespace, stamp=args.stamp,
                              inclusive_all_day_end=args.inclusive_all_day_end,
                              max_events=args.max_events)
        if destination is not None:
            write_calendar(destination, data, force=args.force)
    except CalendarError as error:
        print(f"csv-ics-lite: {error}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as error:
        print(f"csv-ics-lite: unable to read input or write output: {error}", file=sys.stderr)
        return 2
    report = {"ok": True, "events": count, "written": destination is not None}
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(f"{count} event(s) validated" + (" and calendar written" if destination is not None else ""))
    return 0
