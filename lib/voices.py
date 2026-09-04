#!/usr/bin/env python3
"""Carry a voice between meetings, so the next one knows who is talking.

`diarize.py` groups voices inside one meeting only: `Them A` on Monday and on
Tuesday are unrelated. Here the roster in `.voices.json` remembers them.

    {"version": 1, "people": {"Aisha": [{"from": "<id>", "voice": "A", ...}]}}

A person's stored voice is the mean of their samples. Matching is cosine
similarity against that mean — what sherpa's `SpeakerEmbeddingManager` computes,
written out so this module stays stdlib-only and its tests need no model.

The rule everything here serves: never name someone it is not sure about. A
wrong `Them A` is ignorable. A wrong "Aisha:" corrupts his action items.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import diarize
import merge

ROSTER_FILE = ".voices.json"

# Measured on nine real meetings: wrong people peaked at 0.406, the same
# person bottomed at 0.755. See SPEC.md. Re-measure with `qn voices --sweep`.
MATCH_THRESHOLD = 0.65

# Six decimals is below the noise of the model and keeps the file a third of
# the size. A 512-number vector is ~4 KB at this precision.
PRECISION = 6

# Newest samples of one person to keep. Against drift, not size: an old
# headset pulls the average as hard as today's, and the average stops moving
# well before ten anyway.
MAX_SAMPLES = 10


def roster_path(notes_dir: str) -> str:
    return os.path.join(notes_dir, ROSTER_FILE)


def load(notes_dir: str) -> dict[str, list[dict]]:
    """The roster, or an empty one. Never raises: `qn watch` runs this."""
    try:
        with open(roster_path(notes_dir), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    people = data.get("people")
    if not isinstance(people, dict):
        return {}

    clean: dict[str, list[dict]] = {}
    for name, entries in people.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(entries, list):
            continue
        kept = [entry for entry in entries if _is_entry(entry)]
        if kept:
            clean[name.strip()] = kept
    return clean


def _is_entry(entry: object) -> bool:
    """A usable entry: a recording id and a vector of numbers."""
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("from"), str) or not entry["from"]:
        return False
    vector = entry.get("vector")
    return (isinstance(vector, list) and len(vector) > 0
            and all(isinstance(value, (int, float)) for value in vector))


def _slot(entry: dict) -> tuple[str, str]:
    """One voice in one meeting. A pre-slot entry reads as an empty letter."""
    return entry["from"], str(entry.get("voice", ""))


def save(notes_dir: str, people: dict[str, list[dict]]) -> None:
    """Write the roster. Dropping a person leaves the file, not no file."""
    rounded = {
        name: [{"from": entry["from"],
                "voice": str(entry.get("voice", "")),
                "vector": [round(float(value), PRECISION) for value in entry["vector"]]}
               for entry in entries]
        for name, entries in people.items()
    }
    with open(roster_path(notes_dir), "w", encoding="utf-8") as handle:
        json.dump({"people": rounded}, handle)
        handle.write("\n")


def find_name(people: dict[str, list[dict]], name: str) -> str | None:
    """The stored spelling, ignoring case, so `aisha` finds Aisha."""
    wanted = name.strip().casefold()
    for stored in people:
        if stored.casefold() == wanted:
            return stored
    return None


def enrol(people: dict[str, list[dict]], name: str, recording_id: str,
          letter: str, vector: list[float]) -> dict[str, list[dict]]:
    """Teach the roster that this voice, in this meeting, is `name`.

    Keyed by meeting AND letter: the grouping splits one person across
    letters, so one meeting can hold several good samples of them. Naming a
    letter again replaces it, and naming it for someone else moves it, so a
    correction stops the mistake voting.
    """
    name, letter = name.strip(), str(letter).strip().upper()
    if not name or not recording_id or not letter or not vector:
        return people

    slot = (recording_id, letter)
    updated = {
        person: [entry for entry in entries if _slot(entry) != slot]
        for person, entries in people.items()
    }
    stored = find_name(updated, name) or name
    updated.setdefault(stored, [])
    updated[stored].append({"from": recording_id, "voice": letter, "vector": list(vector)})

    # Newest wins, by meeting date. A recording id starts with its date, so
    # sorting the ids sorts the meetings. Sorting rather than trimming the tail
    # matters when you name an old meeting after a new one.
    updated[stored] = sorted(updated[stored], key=_slot)[-MAX_SAMPLES:]

    # A person whose only entry just moved to someone else is no longer known.
    return {person: entries for person, entries in updated.items() if entries}


def forget(people: dict[str, list[dict]], name: str) -> dict[str, list[dict]]:
    """Drop a person from the roster. Notes already written are not touched."""
    stored = find_name(people, name)
    if stored is None:
        return people
    return {person: entries for person, entries in people.items() if person != stored}


def mean(vectors: list[list[float]]) -> list[float]:
    """The average voice of one person. Vectors of odd length are ignored."""
    if not vectors:
        return []
    usable = [vector for vector in vectors if len(vector) == len(vectors[0])]
    count = len(usable)
    return [sum(values) / count for values in zip(*usable)]


def similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity. An all-zero vector matches nothing, never divides."""
    if len(left) != len(right):
        return 0.0
    left_size = math.sqrt(sum(value * value for value in left))
    right_size = math.sqrt(sum(value * value for value in right))
    if left_size == 0.0 or right_size == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_size * right_size)


def scores(people: dict[str, list[dict]], vector: list[float]) -> list[tuple[str, float]]:
    """Every person and how alike this voice is, best first."""
    ranked = [(name, similarity(vector, mean([entry["vector"] for entry in entries])))
              for name, entries in people.items() if entries]
    return sorted(ranked, key=lambda row: (-row[1], row[0]))


def match(people: dict[str, list[dict]], vector: list[float],
          threshold: float) -> str | None:
    """Who this voice is, or None. None is honest: the caller prints the letter."""
    if not vector:
        return None
    ranked = scores(people, vector)
    if not ranked or ranked[0][1] < threshold:
        return None
    return ranked[0][0]


# --------------------------------------------------------------- the meeting

MATCHED_FILE = "matched.txt"
CONFIRMED_FILE = "confirmed.txt"


def read_letters(recording_dir: str, filename: str) -> dict[str, str]:
    """`A=Marco` lines. Parsed by `merge.py`, so both always agree."""
    return merge.read_named(pathlib.Path(recording_dir) / filename)


def write_letters(recording_dir: str, filename: str, named: dict[str, str]) -> None:
    with open(os.path.join(recording_dir, filename), "w", encoding="utf-8") as handle:
        for letter in sorted(named):
            handle.write(f"{letter}={named[letter]}\n")


def match_recording(notes_dir: str, recording_dir: str,
                    threshold: float = MATCH_THRESHOLD) -> dict[str, str]:
    """Name what the roster recognises here. Rewrites matched.txt from scratch.

    Confirmed letters are skipped, so re-running cannot undo your answer, and
    rewriting rather than adding means `qn forget` actually takes effect.
    """
    people = load(notes_dir)
    confirmed = read_letters(recording_dir, CONFIRMED_FILE)

    found = {}
    for letter, vector in sorted(diarize.read_prints(recording_dir).items()):
        if letter in confirmed:
            continue
        name = match(people, vector, threshold)
        if name is not None:
            found[letter] = name

    if found:
        write_letters(recording_dir, MATCHED_FILE, found)
    else:
        try:
            os.remove(os.path.join(recording_dir, MATCHED_FILE))
        except OSError:
            pass
    return found


def talk_seconds(recording_dir: str) -> dict[str, float]:
    """How long each voice group talked, for a list a person has to read."""
    return diarize.talking(diarize.read_speakers(recording_dir))


def spoken(seconds: float) -> str:
    """`3m 25s`, never `0 min`. Rounding hid the number that warns a reader."""
    return f"{int(seconds) // 60}m {int(seconds) % 60:02d}s"


def recordings_dir(notes_dir: str) -> str:
    return os.path.join(notes_dir, ".recordings")


def pending(notes_dir: str) -> list[tuple[str, str, float]]:
    """Voices with a print and no name, the most talkative first.

    Everything listed talked long enough to be worth naming.
    This list is an instruction, and one that harms the reader is a defect.
    """
    root = recordings_dir(notes_dir)
    try:
        meetings = sorted(os.listdir(root), reverse=True)
    except OSError:
        return []

    waiting = []
    for meeting in meetings:
        work = os.path.join(root, meeting)
        if not os.path.isdir(work):
            continue
        named = set(read_letters(work, CONFIRMED_FILE)) | set(read_letters(work, MATCHED_FILE))
        rows = diarize.read_speakers(work)
        seconds, strong = diarize.talking(rows), diarize.worth_remembering(rows)
        for letter in sorted(diarize.read_prints(work)):
            # Short voices are filtered here as well as at build time, so a
            # file written by an earlier version cannot put one back on the
            # list. Nothing this list offers may harm the roster.
            if letter not in named and letter in strong:
                waiting.append((meeting, letter, seconds.get(letter, 0.0)))
    return sorted(waiting, key=lambda row: (-row[2], row[0], row[1]))


def enrol_from(notes_dir: str, recording_dir: str, letter: str, name: str,
               consent: str) -> str:
    """Teach the roster one voice. A meeting you held back teaches nothing.

    `qn` reads the consent file and passes the answer, so one implementation
    of that rule exists rather than two that must agree.
    """
    letter = letter.strip().upper()
    prints = diarize.read_prints(recording_dir)
    rows = diarize.read_speakers(recording_dir)
    spoke = diarize.talking(rows).get(letter)

    # The floor is checked here, not only where prints are built. A file
    # written by an earlier version still holds prints for short voices, and
    # those are exactly the ones that make recognition worse.
    if spoke is not None and letter not in diarize.worth_remembering(rows):
        return (f"voice {letter} talked for {spoken(spoke)} — too little to remember. "
                f"the transcript is named, the roster is unchanged")

    if letter not in prints:
        # Say which reason it is. "No voiceprint" alone sent the reader
        # looking for a setting that was already on.
        if spoke is None:
            return f"no voice {letter} here — this meeting was not grouped by voice"
        return f"no voiceprint for {letter} — rebuild with: qn redo {os.path.basename(recording_dir)}"

    if consent != "full":
        return f"not learning this voice — the meeting is {consent}, not shared"

    people = load(notes_dir)
    updated = enrol(people, name, os.path.basename(recording_dir.rstrip("/")),
                    letter, prints[letter])
    save(notes_dir, updated)

    stored = find_name(updated, name) or name
    kept = updated.get(stored, [])
    samples = len(kept)

    # The cap keeps the newest. Naming an old meeting when the roster is full
    # therefore stores nothing, and saying "learned" would be a lie.
    slot = (os.path.basename(recording_dir.rstrip("/")), letter)
    if slot not in {_slot(entry) for entry in kept}:
        return (f"{stored} already has {samples} newer samples — this one is older, "
                f"so the roster is unchanged. the transcript is named")

    return f"learned {stored}'s voice — {samples} sample{'s' if samples != 1 else ''} on file"


# ------------------------------------------------------------------ printing


def render(notes_dir: str) -> str:
    """The `qn voices` screen: who the roster knows, and who is waiting."""
    people = load(notes_dir)
    lines = ["", "  known voices"]
    if people:
        width = max(len(name) for name in people)
        for name in sorted(people):
            entries = people[name]
            last = max(entry["from"] for entry in entries)
            lines.append(f"    {name:<{width}}  {len(entries)} sample"
                         f"{'s' if len(entries) != 1 else ' '}  last {last[:10]}")
    else:
        lines.append("    nobody yet")

    waiting = pending(notes_dir)
    lines += ["", "  waiting to be named"]
    if waiting:
        width = max(len(meeting) for meeting, _, _ in waiting)
        for meeting, letter, seconds in waiting:
            lines.append(f"    {meeting:<{width}}  {letter}  {spoken(seconds)} of talking")
        lines += ["", '  name one with:  qn confirm <id> <letter> "<name>"',
                  "  hear one first:  qn play <id> <letter>",
                  "",
                  "  one person is often split across letters. naming each one",
                  "  gives the roster another sample of them, which helps."]
    else:
        lines.append("    nobody")

    lines += ["", '  forget a voice with:  qn forget "<name>"', ""]
    return "\n".join(lines)


def sweep(notes_dir: str) -> str:
    """Score every stored voice against every print. How MATCH_THRESHOLD is set."""
    people = load(notes_dir)
    if not people:
        return "no voices on the roster — nothing to sweep"

    root = recordings_dir(notes_dir)
    lines = ["meeting                                    voice  best match       score"]
    for meeting in sorted(os.listdir(root)):
        work = os.path.join(root, meeting)
        for letter, vector in sorted(diarize.read_prints(work).items()):
            ranked = scores(people, vector)
            if not ranked:
                continue
            name, score = ranked[0]
            runner = f"  (next: {ranked[1][0]} {ranked[1][1]:.3f})" if len(ranked) > 1 else ""
            lines.append(f"{meeting:<42} {letter:<6} {name:<15} {score:.3f}{runner}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: voices.py <notes-dir> --list | --match <dir> | "
                 "--enrol <dir> <letter> <consent> <name> | --forget <name> | --sweep")

    where, action = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]

    if action == "--list":
        print(render(where))
    elif action == "--sweep":
        print(sweep(where))
    elif action == "--match" and rest:
        for letter, name in sorted(match_recording(where, rest[0]).items()):
            print(f"{letter}={name}")
    elif action == "--enrol" and len(rest) >= 4:
        print(enrol_from(where, rest[0], rest[1], " ".join(rest[3:]), rest[2]))
    elif action == "--forget" and rest:
        name = " ".join(rest)
        people = load(where)
        stored = find_name(people, name)
        if stored is None:
            sys.exit(f"no voice on file for {name}")
        save(where, forget(people, name))
        print(f"forgot {stored}")
    else:
        sys.exit("usage: voices.py <notes-dir> --list | --match <dir> | "
                 "--enrol <dir> <letter> <consent> <name> | --forget <name> | --sweep")
