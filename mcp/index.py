#!/usr/bin/env python3
"""Search index over the meeting notes in QN_NOTES_DIR.

The index is a cache. Every row comes from a markdown note on disk, so
`.index.db` can be deleted at any time and rebuilt by a rescan.

A note is indexed only when its frontmatter positively reads `sharing: full`.
Everything else — `local`, `none`, a missing key, a malformed block, a value
nobody planned for — stays out. That rule lives in `is_shareable()`, called by
`parse_note()`, the only function that reads a note file, so no caller can go
around it.

Standard library only. Python 3.13.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
from typing import Any

DEFAULT_NOTES_DIR = "~/Meetings"
INDEX_FILENAME = ".index.db"

# SPEC.md: `full` is the one value that permits indexing. `local` keeps the
# transcript on this machine and `none` should not exist at all.
SHARING_FULL = "full"

# Bumped whenever the tables change, so an old file is rebuilt instead of read.
# Version 2 also forces a rescan of every note, because version 1 was written
# by a parser that indexed a note whose sharing value it could not read.
SCHEMA_VERSION = 3

SNIPPET_TOKENS = 14
MAX_TRANSCRIPT_LINES = 2000

_FTS_COLUMNS = {"notes": ("notes_text", 1), "transcript": ("transcript_text", 2)}

_TRANSCRIPT_HEADING = re.compile(r"^##[ \t]+Transcript[ \t]*$", re.MULTILINE)
_FENCE = re.compile(r"^```[^\n]*\n(?P<body>.*?)^```", re.MULTILINE | re.DOTALL)
_SECTION_HEADING = re.compile(r"^##[ \t]+(?P<name>\S.*?)[ \t]*$", re.MULTILINE)
_TITLE_HEADING = re.compile(r"^#[ \t]+(?P<title>\S.*?)[ \t]*$", re.MULTILINE)
_CHECKBOX = re.compile(r"^[ \t]*[-*][ \t]+\[(?P<mark>[ xX])\][ \t]*(?P<text>.*)$")
_OWNER = re.compile(r"^(?P<owner>[^\s:][^:]{0,38}):[ \t]+(?P<rest>\S.*)$")
_ID_DATE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})-(?P<hour>\d{2})(?P<minute>\d{2})-")
_STAMP = re.compile(r"^\[(?P<minutes>\d+):(?P<seconds>\d{2})\]")
_FRONTMATTER_LINE = re.compile(r"^[ \t]*(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)[ \t]*:(?P<value>.*)$")
_BODY_SHARING = re.compile(r"^[ \t]*sharing[ \t]*:", re.MULTILINE | re.IGNORECASE)

# The characters that build an FTS5 column filter or group. A query is wrapped
# in `{column} : (...)`, so a query holding these could close that group and
# name another column.
_FTS_STRUCTURE = str.maketrans({character: " " for character in "{}():"})


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def notes_dir() -> str:
    """Return the notes directory. QN_NOTES_DIR overrides the default."""
    raw = os.environ.get("QN_NOTES_DIR") or DEFAULT_NOTES_DIR
    return os.path.abspath(os.path.expanduser(raw))


def index_path(directory: str | None = None) -> str:
    """Return the index file that belongs to a notes directory."""
    return os.path.join(directory or notes_dir(), INDEX_FILENAME)


# --------------------------------------------------------------------------
# note parsing
# --------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a note into frontmatter fields and body.

    The schema is fixed and tiny (SPEC.md, "Frontmatter fields"), so this reads
    `key: value`, `key: [a, b]` and `key: []` only. It is not a YAML parser and
    must not become one.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text

    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text

    fields: dict[str, Any] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        fields[key.strip()] = _parse_value(raw.strip())
    return fields, "\n".join(lines[end + 1:])


def _parse_value(raw: str) -> Any:
    """Read one frontmatter value: a scalar, or a flow list like [a, b]."""
    if raw.startswith("[") and raw.endswith("]"):
        return [_unquote(part) for part in _split_items(raw[1:-1]) if part]
    return _unquote(raw)


def _split_items(inner: str) -> list[str]:
    """Split a flow list on its commas. A quoted item may contain a comma."""
    items: list[str] = []
    current: list[str] = []
    quote = ""
    for character in inner:
        if quote:
            if character == quote:
                quote = ""
            current.append(character)
        elif character in "\"'":
            quote = character
            current.append(character)
        elif character == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    items.append("".join(current).strip())
    return [item for item in items if item]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def is_shareable(text: str) -> bool:
    """Say whether a note may be indexed. Fail closed on anything unclear.

    Only a frontmatter block that positively reads `sharing: full` opens the
    index. A missing key, an unknown value, a block that never closes, a block
    cut short by a `---` inside a value, or no frontmatter at all all mean
    private, because a private note read wrongly is the one failure this tool
    must never have.

    This reads the raw text instead of `parse_frontmatter()` on purpose. That
    parser is forgiving by design, and forgiveness is what leaks a note.
    """
    lines = text.lstrip("\ufeff").split("\n")  # a BOM must not hide the block
    if lines[0].strip() != "---":
        return False  # no frontmatter block at all

    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return False  # the block never closes

    found: list[str] = []
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _FRONTMATTER_LINE.match(line)
        if match is None:
            return False  # not `key: value`, so this block is not what it seems
        value = match.group("value").strip()
        if value.startswith("[") and not value.endswith("]"):
            # A list cut in half means the `---` above was a stray one inside a
            # value, e.g. an attendee name that carries a newline. The real end
            # of the block, and the real `sharing:` line, are further down.
            return False
        if match.group("key").lower() == "sharing":
            found.append(_sharing_value(value))

    if not found:
        return False  # the note never claimed to be shareable
    if any(value != SHARING_FULL for value in found):
        return False  # a duplicate key gives up the safest value, not the last
    # Last guard: a `sharing:` line in the body says the block ended early.
    return _BODY_SHARING.search("\n".join(lines[end + 1:])) is None


def _sharing_value(raw: str) -> str:
    """Normalise one `sharing:` value: drop a trailing comment, quotes and case."""
    return _unquote(_strip_comment(raw).strip()).strip().lower()


def _strip_comment(value: str) -> str:
    """Drop a trailing ` # comment`. A `#` inside quotes belongs to the value."""
    quote = ""
    for position, character in enumerate(value):
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == "#" and (position == 0 or value[position - 1] in " \t"):
            return value[:position]
    return value


def split_note(body: str) -> tuple[str, str]:
    """Split a note body into (notes text, transcript text).

    The transcript sits after the final `---` rule, under `## Transcript`,
    inside a fenced block. The fence markers are dropped; the lines are not.
    """
    match = _TRANSCRIPT_HEADING.search(body)
    if match is None:
        return body.strip(), ""

    notes = body[:match.start()].rstrip()
    if notes.endswith("---"):
        notes = notes[:-3].rstrip()

    tail = body[match.end():]
    fence = _FENCE.search(tail)
    transcript = fence.group("body") if fence else tail
    return notes, transcript.strip("\n")


def split_sections(notes_text: str) -> dict[str, str]:
    """Return `## ` headings mapped to their text, in document order."""
    sections: dict[str, str] = {}
    headings = list(_SECTION_HEADING.finditer(notes_text))
    for position, heading in enumerate(headings):
        following = headings[position + 1].start() if position + 1 < len(headings) else len(notes_text)
        sections[heading.group("name")] = notes_text[heading.end():following].strip()
    return sections


def parse_actions(sections: dict[str, str]) -> list[dict[str, Any]]:
    """Pull checkbox lines out of the two action-item sections."""
    items: list[dict[str, Any]] = []
    for name, text in sections.items():
        whose = _whose(name)
        if whose is None:
            continue
        for line in text.split("\n"):
            match = _CHECKBOX.match(line)
            if match is None:
                continue
            body = match.group("text").strip()
            if not body:
                continue
            owner, body = _split_owner(body)
            items.append({
                "whose": whose,
                "owner": owner,
                "text": body,
                "done": match.group("mark").lower() == "x",
            })
    return items


def _whose(section_name: str) -> str | None:
    """Map a section heading to mine/theirs, or None when it holds no actions."""
    lowered = section_name.lower()
    if "action item" not in lowered:
        return None
    if lowered.startswith("my"):
        return "mine"
    if lowered.startswith("their"):
        return "theirs"
    return None


def _split_owner(text: str) -> tuple[str | None, str]:
    """Split "Priya: send the doc" into an owner and the rest.

    Only a short capitalised prefix counts, so a sentence with a colon in it
    keeps its full text and reports no owner.
    """
    match = _OWNER.match(text)
    if match is None:
        return None, text
    owner = match.group("owner").strip()
    if len(owner.split()) > 3 or not owner[:1].isupper():
        return None, text
    return owner, match.group("rest").strip()


def parse_note(path: str) -> dict[str, Any] | None:
    """Read one note file. Return None unless it positively says `sharing: full`."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    if not is_shareable(text):
        return None

    fields, body = parse_frontmatter(text)
    meeting_id = os.path.splitext(os.path.basename(path))[0]
    notes_text, transcript_text = split_note(body)
    sections = split_sections(notes_text)
    return {
        "id": meeting_id,
        "title": _title_of(fields, notes_text, meeting_id),
        "date": _date_of(fields, meeting_id),
        "attendees": _attendees_of(fields),
        "sharing": SHARING_FULL,  # is_shareable() let nothing else through
        "notes_text": notes_text,
        "transcript_text": transcript_text,
        "actions": parse_actions(sections),
    }


def _title_of(fields: dict[str, Any], notes_text: str, meeting_id: str) -> str:
    title = str(fields.get("title", "")).strip()
    if title:
        return title
    heading = _TITLE_HEADING.search(notes_text)
    if heading is not None:
        return heading.group("title")
    return meeting_id


def _date_of(fields: dict[str, Any], meeting_id: str) -> str:
    """Return `YYYY-MM-DD HH:MM`. The id carries the same stamp as a fallback."""
    date = str(fields.get("date", "")).strip()
    if date:
        return date
    match = _ID_DATE.match(meeting_id)
    if match is None:
        return ""
    return f"{match.group('day')} {match.group('hour')}:{match.group('minute')}"


def _attendees_of(fields: dict[str, Any]) -> list[str]:
    value = fields.get("attendees", [])
    if isinstance(value, list):
        return [str(name).strip() for name in value if str(name).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_schema(connection: sqlite3.Connection, *, rebuild: bool) -> None:
    """Create the tables, dropping an older layout rather than migrating it."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if rebuild or version != SCHEMA_VERSION:
        connection.executescript(
            "DROP TABLE IF EXISTS actions;"
            "DROP TABLE IF EXISTS meetings_fts;"
            "DROP TABLE IF EXISTS meetings;"
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meetings (
            id              TEXT PRIMARY KEY,
            title           TEXT NOT NULL,
            date            TEXT NOT NULL,
            attendees       TEXT NOT NULL,
            sharing         TEXT NOT NULL,
            fingerprint     TEXT NOT NULL,
            notes_text      TEXT NOT NULL,
            transcript_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS actions (
            meeting_id TEXT NOT NULL REFERENCES meetings(id),
            whose      TEXT NOT NULL,
            owner      TEXT,
            text       TEXT NOT NULL,
            done       INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS actions_by_meeting ON actions(meeting_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
            meeting_id UNINDEXED,
            notes_text,
            transcript_text,
            tokenize='porter unicode61'
        );
        """
    )
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _delete_note(connection: sqlite3.Connection, meeting_id: str) -> None:
    connection.execute("DELETE FROM actions WHERE meeting_id = ?", (meeting_id,))
    connection.execute("DELETE FROM meetings_fts WHERE meeting_id = ?", (meeting_id,))
    connection.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def _write_note(connection: sqlite3.Connection, note: dict[str, Any], fingerprint: str) -> None:
    _delete_note(connection, note["id"])
    connection.execute(
        "INSERT INTO meetings (id, title, date, attendees, sharing, fingerprint,"
        " notes_text, transcript_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            note["id"], note["title"], note["date"],
            json.dumps(note["attendees"], ensure_ascii=False),
            note["sharing"], fingerprint, note["notes_text"], note["transcript_text"],
        ),
    )
    connection.execute(
        "INSERT INTO meetings_fts (meeting_id, notes_text, transcript_text) VALUES (?, ?, ?)",
        (note["id"], note["notes_text"], note["transcript_text"]),
    )
    connection.executemany(
        "INSERT INTO actions (meeting_id, whose, owner, text, done) VALUES (?, ?, ?, ?, ?)",
        [
            (note["id"], item["whose"], item["owner"], item["text"], int(item["done"]))
            for item in note["actions"]
        ],
    )


def _fingerprint(path: str) -> str | None:
    """A hash of the file's bytes. None when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def refresh(db_path: str, notes_dir: str, *, rebuild: bool = False) -> dict[str, int]:
    """Bring the index in line with the notes on disk.

    Incremental, but keyed on a hash of the file rather than its timestamp.
    A timestamp can stay still while the contents change — `touch -r`, a
    restored backup, a sync tool — and that would keep a note in the index
    after it was edited to say `sharing: local`. Hashing costs a read of some
    small markdown files and removes the whole class of problem.

    Notes that vanished, and notes that stopped saying `sharing: full`, are
    dropped. A full rebuild produces the same rows, so a suspect index needs
    no reasoning about — pass rebuild=True.
    """
    directory = os.path.abspath(os.path.expanduser(notes_dir))
    os.makedirs(directory, exist_ok=True)

    connection = _connect(db_path)
    try:
        _ensure_schema(connection, rebuild=rebuild)
        known = {
            row["id"]: row["fingerprint"]
            for row in connection.execute("SELECT id, fingerprint FROM meetings")
        }

        seen: set[str] = set()
        indexed = 0
        for path in sorted(glob.glob(os.path.join(directory, "*.md"))):
            meeting_id = os.path.splitext(os.path.basename(path))[0]
            fingerprint = _fingerprint(path)
            if fingerprint is not None and known.get(meeting_id) == fingerprint:
                # Identical bytes, so the sharing decision is identical too.
                seen.add(meeting_id)
                continue
            # An excluded note is read on every refresh, because only its
            # contents say that it is excluded. Reading a note is cheap.
            note = parse_note(path)
            if note is None:
                continue
            _write_note(connection, note, fingerprint or "")
            seen.add(meeting_id)
            indexed += 1

        removed = [meeting_id for meeting_id in known if meeting_id not in seen]
        for meeting_id in removed:
            _delete_note(connection, meeting_id)

        connection.commit()
        total = connection.execute("SELECT count(*) FROM meetings").fetchone()[0]
    finally:
        connection.close()

    return {"indexed": indexed, "removed": len(removed), "total": total}


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------

def _meetings(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["id"]: {
            "id": row["id"],
            "title": row["title"],
            "date": row["date"],
            "attendees": json.loads(row["attendees"]),
            "sharing": row["sharing"],
        }
        for row in connection.execute("SELECT * FROM meetings")
    }


def _keeps(meeting: dict[str, Any], from_: str | None, to: str | None, with_: str | None) -> bool:
    """Apply the date and attendee filters shared by search and actions."""
    day = meeting["date"][:10]
    if from_ and day < from_:
        return False
    if to and day > to:
        return False
    if with_:
        wanted = with_.strip().lower()
        if not any(wanted in name.lower() for name in meeting["attendees"]):
            return False
    return True


def _quote_query(query: str) -> str:
    """Turn free text into an FTS5 phrase, for input the parser rejects."""
    terms = [term for term in re.findall(r"[\w']+", query) if term]
    return " OR ".join(f'"{term}"' for term in terms)


def _plain_query(query: str) -> str:
    """Strip the characters that let a query break out of its column filter.

    `{`, `}` and `:` name a column, and `(`, `)` close the group the caller
    opens. Without them a query can only ever match the column it was aimed
    at, so `matched_in` stays true. Words, quoted phrases and the AND/OR/NOT
    operators all survive, so ordinary queries read the same as before.
    """
    return query.translate(_FTS_STRUCTURE)


def _column_hits(connection: sqlite3.Connection, query: str, field: str) -> list[tuple[str, str, float]]:
    """Run the query against one FTS column. Returns (id, snippet, rank)."""
    column, position = _FTS_COLUMNS[field]
    sql = (
        f"SELECT meeting_id, snippet(meetings_fts, {position}, '', '', ' … ', {SNIPPET_TOKENS})"
        " AS snip, bm25(meetings_fts) AS rank FROM meetings_fts"
        " WHERE meetings_fts MATCH ? ORDER BY rank"
    )
    for text in (_plain_query(query), _quote_query(query)):
        expression = f"{{{column}}} : ({text})"
        try:
            rows = connection.execute(sql, (expression,)).fetchall()
        except sqlite3.OperationalError:
            continue  # the user's syntax was not FTS5 syntax; try it as text
        return [(row["meeting_id"], row["snip"], row["rank"]) for row in rows]
    return []


def search(
    db_path: str,
    query: str,
    from_: str | None = None,
    to: str | None = None,
    with_: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Find meetings by keyword. Returns pointers and snippets, never documents.

    A meeting whose notes match is reported as `matched_in: notes`, because the
    notes answer most questions on their own. Only a meeting that matches
    nowhere but the transcript is reported as `matched_in: transcript`.
    """
    if not str(query).strip():
        raise ValueError("query must not be empty")
    limit = max(1, int(limit))

    connection = _connect(db_path)
    try:
        meetings = _meetings(connection)
        hits: dict[str, dict[str, Any]] = {}
        # Notes first, so a meeting matching both fields keeps matched_in=notes.
        for field in ("notes", "transcript"):
            for meeting_id, snip, rank in _column_hits(connection, query, field):
                meeting = meetings.get(meeting_id)
                if meeting is None or meeting_id in hits:
                    continue
                if not _keeps(meeting, from_, to, with_):
                    continue
                hits[meeting_id] = {
                    "id": meeting["id"],
                    "title": meeting["title"],
                    "date": meeting["date"],
                    "attendees": meeting["attendees"],
                    "matched_in": field,
                    "snippet": " ".join(snip.split()),
                    "_rank": rank,
                }
    finally:
        connection.close()

    ordered = sorted(hits.values(), key=lambda hit: (hit["_rank"], hit["date"]))
    shown = ordered[:limit]
    for hit in shown:
        hit.pop("_rank")
    return {"total": len(ordered), "shown": len(shown), "results": shown}


def get(db_path: str, meeting_id: str, section: str | None = None) -> dict[str, Any]:
    """Return one meeting's notes, split into sections. Never the transcript."""
    connection = _connect(db_path)
    try:
        row = connection.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise LookupError(f"no indexed meeting with id {meeting_id!r}")

    sections = split_sections(row["notes_text"])
    if section:
        wanted = section.strip().lower()
        sections = {name: text for name, text in sections.items() if name.lower() == wanted}
        if not sections:
            raise LookupError(f"meeting {meeting_id!r} has no section {section!r}")
    return {
        "id": row["id"],
        "title": row["title"],
        "date": row["date"],
        "attendees": json.loads(row["attendees"]),
        "sharing": row["sharing"],
        "sections": sections,
    }


def _stamp_seconds(line: str) -> int | None:
    """Read `[MM:SS]` from a transcript line. Minutes may exceed 59."""
    match = _STAMP.match(line)
    if match is None:
        return None
    return int(match.group("minutes")) * 60 + int(match.group("seconds"))


def transcript(
    db_path: str,
    meeting_id: str,
    around: str | None = None,
    window: int = 60,
) -> dict[str, Any]:
    """Return transcript lines, optionally only those near a timestamp."""
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT id, title, transcript_text FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise LookupError(f"no indexed meeting with id {meeting_id!r}")

    lines = [line for line in row["transcript_text"].split("\n") if line.strip()]
    total = len(lines)

    if around:
        centre = _stamp_seconds(f"[{around.strip().lstrip('[').rstrip(']')}]")
        if centre is None:
            raise ValueError(f"around must look like MM:SS, got {around!r}")
        span = max(0, int(window))
        lines = _lines_near(lines, centre, span)

    truncated = False
    if len(lines) > MAX_TRANSCRIPT_LINES:
        lines = lines[:MAX_TRANSCRIPT_LINES]
        truncated = True
    return {
        "id": row["id"],
        "title": row["title"],
        "lines": lines,
        "truncated": truncated or len(lines) < total,
    }


def _lines_near(lines: list[str], centre: int, span: int) -> list[str]:
    """Keep lines stamped within `span` seconds of `centre`.

    A line with no stamp continues the line above it, so it inherits its time.
    """
    kept: list[str] = []
    current = None
    for line in lines:
        stamp = _stamp_seconds(line)
        if stamp is not None:
            current = stamp
        if current is not None and abs(current - centre) <= span:
            kept.append(line)
    return kept


def actions(
    db_path: str,
    status: str = "open",
    whose: str = "mine",
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List action items across meetings, newest meeting first."""
    if status not in ("open", "done", "all"):
        raise ValueError(f"status must be open, done or all, got {status!r}")
    if whose not in ("mine", "theirs", "all"):
        raise ValueError(f"whose must be mine, theirs or all, got {whose!r}")
    limit = max(1, int(limit))

    clauses = []
    parameters: list[Any] = []
    if status != "all":
        clauses.append("a.done = ?")
        parameters.append(int(status == "done"))
    if whose != "all":
        clauses.append("a.whose = ?")
        parameters.append(whose)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT a.meeting_id, m.date, m.title, m.attendees, a.whose, a.owner,"
            f" a.text, a.done FROM actions a JOIN meetings m ON m.id = a.meeting_id {where}"
            " ORDER BY m.date DESC, a.meeting_id, a.rowid",
            parameters,
        ).fetchall()
    finally:
        connection.close()

    items = []
    for row in rows:
        meeting = {"date": row["date"], "attendees": json.loads(row["attendees"])}
        if not _keeps(meeting, from_, to, None):
            continue
        items.append({
            "id": row["meeting_id"],
            "date": row["date"],
            "meeting_title": row["title"],
            "whose": row["whose"],
            "owner": row["owner"],
            "text": row["text"],
            "done": bool(row["done"]),
        })
    return {"total": len(items), "shown": min(len(items), limit), "items": items[:limit]}


def main(argv: list[str]) -> int:
    """Refresh the index: `python3 mcp/index.py [notes-dir] [--rebuild]`.

    `qn index` calls this with the notes directory as its only argument.
    """
    rebuild = "--rebuild" in argv
    given = [argument for argument in argv if not argument.startswith("-")]
    directory = os.path.abspath(os.path.expanduser(given[0])) if given else notes_dir()
    stats = refresh(index_path(directory), directory, rebuild=rebuild)
    print(f"{stats['total']} meetings indexed in {index_path(directory)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
