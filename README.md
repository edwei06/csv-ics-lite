# csv-ics-lite

Convert an event spreadsheet export into a portable ICS file, entirely offline.

A small Python CLI with named CSV columns, explicit timestamp offsets, all-day
events, stable event UIDs, and strict validation before writing output.
Requires Python 3.10+; no runtime dependencies or calendar-account access.

## Quick start

~~~sh
python -m pip install .
csv-ics-lite examples/events.csv --check
csv-ics-lite examples/events.csv events.ics --namespace community
~~~

Import the generated file with your calendar application's ICS import feature.
Client-specific duplicate/update behavior varies; stable UIDs help identify events
but do not guarantee that repeated manual imports replace existing entries.

## CSV schema

Required headers: `id,summary,start,end`. Optional: `description,location`.
Column order is flexible. Headers are case-sensitive; unknown or duplicate
headers, empty data, blank records, and mismatched field counts are rejected.
Files must be UTF-8; a leading BOM is accepted. Quoted multiline text is supported.

- `id`: unique nonempty identifier with no whitespace; keep it stable when editing.
- `summary`: nonblank event title.
- `start` and `end`: both dates, or both whole-second offset timestamps.
- `description` and `location`: optional plain text.

For timed events, use `2026-10-10T14:00:00+08:00` or
`2026-10-10T06:00:00Z`. Output is normalized to UTC. Naive times, unknown
`-00:00` offsets, fractional seconds, and leap seconds are rejected.
Provide the correct offset for each event date; named timezone/DST inference is
outside this tool's scope.

For all-day events, use `YYYY-MM-DD`. The default end is **exclusive**: an event
on October 11 uses start `2026-10-11`, end `2026-10-12`.
If your source uses inclusive end dates, pass `--inclusive-all-day-end`;
the converter adds one day. End must follow start after normalization.

## Output guarantees and boundaries

Each event has a UUID-based UID derived from the exact ID and namespace, plus
DTSTAMP, DTSTART, DTEND, and SUMMARY. CSV row order is preserved. Editing the title
or time keeps the UID stable; changing the namespace creates different UIDs.

Text escapes backslashes, commas, semicolons, and newlines. Output uses UTF-8 and
CRLF. Long lines fold at Unicode character boundaries with at most 75 bytes per
physical line, including continuation spaces. Plain text cannot inject an extra
calendar property through a newline.

Validation finishes before output creation. Existing output requires `--force`,
which writes a temporary file in the destination directory and then replaces the
output. Input cannot be overwritten, even with `--force`. For a new output,
exclusive creation prevents overwriting an existing file.

There is no recurrence, alarm, invitation, attendee, attachment, HTML description,
network synchronization, or timezone database support. Calendar applications have
not been individually tested. This focused serializer implements the relevant
RFC 5545 event subset, not every calendar feature.

## Options and exit codes

| Option | Purpose |
| --- | --- |
| `--check` | Validate without writing; omit the output path |
| `--force` | Allow replacement of an existing output |
| `--namespace TEXT` | UID namespace; keep stable across exports (default: default) |
| `--stamp TIMESTAMP` | Fixed DTSTAMP for reproducible builds |
| `--inclusive-all-day-end` | Convert inclusive source end dates |
| `--max-events N` | Limit events (default: 10,000) |
| `--max-bytes N` | Limit input size (default: 8 MiB) |
| `--format text\|json` | Select validation summary format |

CSV fields also use Python's default CSV field-size limit (normally 128 KiB).
Exit 0 means success, 1 means invalid event data, and 2 means arguments or
filesystem failure. Errors identify rows/fields without echoing event values.
The ICS file itself intentionally contains titles, descriptions, and locations.

## Development

~~~sh
python -m unittest discover -s tests -v
~~~

Tests cover offsets, exclusive ends, Unicode folding, escaping, stable UIDs,
malformed input, and output preservation. See [CONTRIBUTING.md](CONTRIBUTING.md)
and [LICENSE](LICENSE).

Related work: [csv2ical](https://github.com/rlan/csv2ical).
Format reference: [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545).
Implementation and synthetic examples are independently authored.
