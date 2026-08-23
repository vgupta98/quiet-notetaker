#!/usr/bin/env python3
"""Remember who you meet, so Claude can put names to lines.

The transcript carries two speakers: `Me` from the microphone, and `Them` from
everything the call played back. `Them` is a mixdown of every other voice, so
no amount of audio work splits it into people. The names have to come from
context — who greets whom, who is asked for an update. Claude does that better
when it already knows who these people are.

So this module keeps a plain markdown roster next to the notes:

    - **Priya Sharma** (4 meetings, last 2026-08-22) — my manager, owns billing

The bracket is ours and gets rewritten after every recording. Everything after
the dash is yours and is never touched. That free text is the whole point. It
tells Claude more about who said a line than any count can.

Two rules keep this honest:

  Only `sharing: full` notes contribute a name. A held meeting keeps its
  attendees to itself, because the roster goes back to Claude later.

  A name is never guessed from audio. It comes from `--with` or from your
  calendar, so a person typed it. The same rule that keeps `vocab.py` from
  learning its own mistakes applies here: nothing the machine invented is
  allowed to become memory.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp"))

import index  # noqa: E402  (path set above)

PEOPLE_FILE = "people.md"
REMOVED_FILE = ".people-removed"
SUGGESTED_FILE = ".people-suggested"

# The roster is a file a person reads. The context block is part of a prompt.
# Both need a stated limit rather than a surprising one.
MAX_PEOPLE = 500
MAX_CONTEXT = 20

# A name arrives from a calendar invite, so treat it as hostile. These would
# break the roster line back into fields, or end the prompt block early.
UNSAFE = re.compile(r"[\x00-\x1f\x7f*()\[\]—]")

LINE = re.compile(
    r"^-[ \t]+\*\*(?P<name>[^*]+?)\*\*"             # - **Name**
    r"(?:[ \t]*\((?P<stats>[^)]*)\))?"              # (4 meetings, last 2026-08-22)
    r"(?:[ \t]*(?:—|--|-)[ \t]*(?P<note>.*?))?"     # — anything you wrote
    r"[ \t]*$"
)

HEADER = """# People in your meetings.
#
# The bracket after each name is rebuilt after every recording. Anything you
# write after the dash is yours, and stays. Use it to tell Claude who someone
# is — it is what turns "Them" into a name:
#
#   - **Priya Sharma** (4 meetings, last 2026-08-22) — my manager, owns billing
#
# Delete a line to drop that person for good. Add your own freely.
"""


@dataclass
class Person:
    name: str
    note: str = ""
    meetings: set[str] = field(default_factory=set)
    last: str = ""
    # What the file said last time. Kept so a plain read of people.md still
    # shows the counts, without re-parsing every note to rebuild them.
    recorded: str = ""

    @property
    def key(self) -> str:
        return self.name.casefold()

    def stats(self) -> str:
        """The bracket. Empty for someone you added who has not turned up yet."""
        if not self.meetings:
            return self.recorded
        count = len(self.meetings)
        word = "meeting" if count == 1 else "meetings"
        if self.last:
            return f"{count} {word}, last {self.last}"
        return f"{count} {word}"

    def render(self) -> str:
        line = f"- **{self.name}**"
        stats = self.stats()
        if stats:
            line += f" ({stats})"
        if self.note:
            line += f" — {self.note}"
        return line


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    """A name that cannot break the roster line or the prompt block."""
    flat = UNSAFE.sub(" ", raw).strip().strip("-").strip()
    return " ".join(flat.split())


def split_attendees(raw: str) -> list[str]:
    """Read a `--with` string or an attendees.txt file into names."""
    names: list[str] = []
    seen: set[str] = set()
    for piece in re.split(r"[,\n;]", raw or ""):
        name = clean_name(piece)
        if len(name) < 2 or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        names.append(name)
    return names


def _tokens(name: str) -> frozenset[str]:
    return frozenset(word for word in re.split(r"\W+", name.casefold()) if word)


def same_person(one: str, other: str) -> bool:
    """True when two spellings plainly mean the same person.

    Only used to look a name up, never to merge two roster entries. "Priya"
    matches both "Priya Sharma" and "Priya Patel", so merging on this would
    quietly fuse two colleagues into one.
    """
    left, right = _tokens(one), _tokens(other)
    if not left or not right:
        return False
    return left <= right or right <= left


# --------------------------------------------------------------------------
# building the roster
# --------------------------------------------------------------------------

def harvest(notes: list[dict]) -> list[Person]:
    """Count who attended what. `notes` are dicts from index.parse_note.

    Private meetings are already absent, because parse_note refuses them.
    """
    found: dict[str, Person] = {}
    for note in sorted(notes, key=lambda item: item.get("id", "")):
        day = str(note.get("date", ""))[:10]
        for raw in note.get("attendees", []):
            name = clean_name(str(raw))
            if len(name) < 2:
                continue
            person = found.setdefault(name.casefold(), Person(name=name))
            person.meetings.add(note.get("id", ""))
            if day > person.last:
                person.last = day
    return sorted(found.values(), key=lambda person: (-len(person.meetings), person.key))


def parse_roster(text: str) -> list[Person]:
    """Read people.md back, keeping the order and the free text you wrote."""
    people: list[Person] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = LINE.match(stripped)
        if match is None:
            continue
        name = clean_name(match.group("name"))
        if len(name) < 2 or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        people.append(Person(name=name, note=(match.group("note") or "").strip(),
                             recorded=(match.group("stats") or "").strip()))
    return people


def read_roster(directory: str) -> list[Person]:
    path = os.path.join(directory, PEOPLE_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return parse_roster(handle.read())


def merge(existing: list[Person], harvested: list[Person], removed: set[str]) -> list[Person]:
    """Keep what you wrote, refresh the counts, add whoever is new.

    A person you typed in yourself always survives, even one you deleted
    before. The removal list only ever silences our own additions.
    """
    counts = {person.key: person for person in harvested}
    out: list[Person] = []
    seen: set[str] = set()

    for person in existing:
        if person.key in seen:
            continue
        seen.add(person.key)
        fresh = counts.get(person.key)
        if fresh is not None:
            person.meetings = set(fresh.meetings)
            person.last = fresh.last
            person.recorded = ""
        out.append(person)

    for person in harvested:
        if person.key in seen or person.key in removed:
            continue
        seen.add(person.key)
        out.append(person)

    return out[:MAX_PEOPLE]


def load_removed(directory: str) -> set[str]:
    """People you deleted. They never come back."""
    path = os.path.join(directory, REMOVED_FILE)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip().casefold() for line in handle if line.strip()}


def _previous(directory: str) -> set[str]:
    path = os.path.join(directory, SUGGESTED_FILE)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip().casefold() for line in handle if line.strip()}


def _remember(directory: str, people: list[Person]) -> None:
    with open(os.path.join(directory, SUGGESTED_FILE), "w", encoding="utf-8") as handle:
        for person in people:
            handle.write(person.name + "\n")


def write_roster(directory: str, people: list[Person]) -> None:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, PEOPLE_FILE)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(HEADER)
        for person in people:
            handle.write(person.render() + "\n")


def refresh(directory: str) -> list[Person]:
    """Re-count, merge, and record anyone you deleted since last time."""
    notes = []
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if name.endswith(".md") and name != PEOPLE_FILE:
                note = index.parse_note(os.path.join(directory, name))
                if note is not None:
                    notes.append(note)

    before = read_roster(directory)
    harvested = harvest(notes)
    removed = load_removed(directory)

    # Someone we added before, who is no longer in the file, was deleted on
    # purpose. Remember that, or the next recording puts them straight back.
    deleted = _previous(directory) - {person.key for person in before} - removed
    if deleted:
        removed |= deleted
        with open(os.path.join(directory, REMOVED_FILE), "a", encoding="utf-8") as handle:
            for key in sorted(deleted):
                handle.write(key + "\n")

    merged = merge(before, harvested, removed)
    write_roster(directory, merged)
    # Remember only what WE added. Recording the merged list would turn your
    # own entries into ours, so deleting one would tombstone it.
    _remember(directory, [person for person in harvested if person.key not in removed])
    return merged


# --------------------------------------------------------------------------
# the prompt block
# --------------------------------------------------------------------------

def context(directory: str, attendees: str) -> str:
    """The people block handed to Claude with a transcript.

    Every attendee is listed, whether or not the roster knows them, because
    prompt.md forbids Claude from using a name that is not on this list.
    """
    names = split_attendees(attendees)
    if not names:
        return ""

    roster = read_roster(directory)
    lines: list[str] = []
    for name in names[:MAX_CONTEXT]:
        match = next((person for person in roster if same_person(person.name, name)), None)
        if match is None:
            lines.append(name)
            continue
        detail = ", ".join(part for part in (match.note, match.stats()) if part)
        lines.append(f"{match.name} — {detail}" if detail else match.name)

    if len(names) > MAX_CONTEXT:
        lines.append(f"and {len(names) - MAX_CONTEXT} more")
    return "\n".join(lines)


if __name__ == "__main__":
    where = sys.argv[1] if len(sys.argv) > 1 else index.notes_dir()
    if "--context" in sys.argv:
        position = sys.argv.index("--context")
        given = sys.argv[position + 1] if position + 1 < len(sys.argv) else ""
        print(context(where, given))
    else:
        for entry in refresh(where):
            print(entry.render())
