"""A focused RFC 5545 event serializer with strict CSV inputs."""
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
import re
import uuid

REQUIRED = {"id", "summary", "start", "end"}
OPTIONAL = {"description", "location"}
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
INSTANT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})$")


class CalendarError(ValueError):
    """The input does not satisfy the supported event schema."""


def _text(value, field, line):
    if any((ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127 for char in value):
        raise CalendarError(f"line {line}: unsupported control character in {field}")
    return value


def parse_instant(value):
    if not INSTANT.fullmatch(value) or value.endswith("-00:00"):
        raise CalendarError("Use a whole-second timestamp with a known UTC offset or Z")
    if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise CalendarError("UTC offset hours or minutes are invalid")
    try:
        # Python 3.10 requires a numeric offset rather than the Z suffix.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise CalendarError("Timestamp or UTC conversion is outside the supported range") from error


def format_date(value):
    return f"{value.year:04d}{value.month:02d}{value.day:02d}"


def format_instant(value):
    return format_date(value) + f"T{value.hour:02d}{value.minute:02d}{value.second:02d}Z"


def _period(start, end, inclusive_all_day_end, line):
    try:
        if DATE.fullmatch(start) and DATE.fullmatch(end):
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if inclusive_all_day_end:
                last += timedelta(days=1)
            if last <= first:
                raise CalendarError("End must follow start")
            return "DATE", format_date(first), format_date(last)
        if DATE.fullmatch(start) or DATE.fullmatch(end):
            raise CalendarError("Start and end must both be dates or both be offset timestamps")
        first, last = parse_instant(start), parse_instant(end)
        if last <= first:
            raise CalendarError("End must follow start in UTC")
        return "DATE-TIME", format_instant(first), format_instant(last)
    except (CalendarError, ValueError, OverflowError) as error:
        message = str(error) if isinstance(error, CalendarError) else "Invalid date or date arithmetic"
        raise CalendarError(f"line {line}: {message}") from error


def parse_events(text, *, inclusive_all_day_end=False, max_events=10000):
    if max_events < 1:
        raise CalendarError("Event limit must be positive")
    reader = csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    try:
        headers = next(reader, None)
        if not headers or len(headers) != len(set(headers)) or any(not header for header in headers):
            raise CalendarError("CSV requires unique nonempty column names")
        if not REQUIRED.issubset(headers):
            raise CalendarError("Required columns: id,summary,start,end")
        if set(headers) - REQUIRED - OPTIONAL:
            raise CalendarError("Unknown columns; supported optional columns: description,location")
        seen, events = set(), []
        for fields in reader:
            line = reader.line_num
            if len(fields) != len(headers):
                raise CalendarError(f"line {line}: field count does not match the header")
            if len(events) >= max_events:
                raise CalendarError("Event limit exceeded")
            row = dict(zip(headers, fields))
            for field, value in row.items():
                _text(value, field, line)
            identifier = row["id"]
            if not identifier or identifier != identifier.strip() or any(char.isspace() for char in identifier):
                raise CalendarError(f"line {line}: id must be nonempty and contain no whitespace")
            if identifier in seen:
                raise CalendarError(f"line {line}: duplicate event id")
            seen.add(identifier)
            if not row["summary"].strip():
                raise CalendarError(f"line {line}: summary must be nonempty")
            kind, start, end = _period(row["start"], row["end"], inclusive_all_day_end, line)
            events.append({"id": identifier, "summary": row["summary"],
                           "description": row.get("description", ""),
                           "location": row.get("location", ""),
                           "kind": kind, "start": start, "end": end})
        if not events:
            raise CalendarError("CSV contains no events")
        return events
    except csv.Error as error:
        raise CalendarError(f"line {reader.line_num}: malformed CSV or field-size limit exceeded") from error


def escape_text(value):
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold_line(line):
    """Fold at Unicode character boundaries; continuation spaces count as bytes."""
    chunks, current, size = [], "", 0
    for character in line:
        width = len(character.encode("utf-8"))
        if size + width > 75:
            chunks.append(current)
            current, size = " ", 1
        current += character
        size += width
    chunks.append(current)
    return "\r\n".join(chunks)


def event_uid(namespace, identifier):
    payload = "csv-ics-lite:" + json.dumps([namespace, identifier], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def render_calendar(events, *, namespace="default", stamp=None):
    if not namespace.strip() or any(ord(char) < 32 or ord(char) == 127 for char in namespace):
        raise CalendarError("Namespace must be nonempty and contain no control characters")
    generated = parse_instant(stamp) if stamp is not None else datetime.now(timezone.utc).replace(microsecond=0)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//edwei06//csv-ics-lite 0.1.0//EN",
             "CALSCALE:GREGORIAN"]
    for event in events:
        lines += ["BEGIN:VEVENT", "UID:" + event_uid(namespace, event["id"]),
                  "DTSTAMP:" + format_instant(generated)]
        suffix = ";VALUE=DATE" if event["kind"] == "DATE" else ""
        lines += ["DTSTART" + suffix + ":" + event["start"],
                  "DTEND" + suffix + ":" + event["end"],
                  "SUMMARY:" + escape_text(event["summary"])]
        for field in ("description", "location"):
            if event[field]:
                lines.append(field.upper() + ":" + escape_text(event[field]))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold_line(line) for line in lines) + "\r\n").encode("utf-8")


def convert(text, **options):
    events = parse_events(text, inclusive_all_day_end=options.get("inclusive_all_day_end", False),
                          max_events=options.get("max_events", 10000))
    calendar = render_calendar(events, namespace=options.get("namespace", "default"),
                               stamp=options.get("stamp"))
    return calendar, len(events)
