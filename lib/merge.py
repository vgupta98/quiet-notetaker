"""Put the two tracks back into one conversation, in the order it was said.

The tracks are transcribed apart so that each line knows who spoke. This puts
them back together, which is the only place the meeting exists as a whole.

Usage: python3 merge.py <recording-dir>   -> transcript on stdout
"""

from __future__ import annotations

import json
import pathlib
import sys

TRACKS = (("them", "Them"), ("me", "Me"))

# Voice groups from diarize.py, when it ran. `Them A` and `Them B` are a hint
# for Claude, never an identity — see the module docstring in diarize.py.
SPEAKERS_FILE = "speakers.json"

# What you said a voice really was. You were in the room, so this outranks
# everything else here.
CONFIRMED_FILE = "confirmed.txt"

# Who the voice roster recognised, from a name you confirmed in an earlier
# meeting. Second-best evidence, and kept in its own file so that re-matching
# can never overwrite an answer you gave yourself.
MATCHED_FILE = "matched.txt"

# One speaker talking for two minutes is one turn to whisper and a wall of text
# to a reader. A pause this long is where a paragraph should break.
TURN_BREAK_MS = 8_000


def read_voices(recording_dir: pathlib.Path) -> list[tuple[int, int, str]]:
    """Voice groups from diarize.py. Empty when it never ran."""
    path = recording_dir / SPEAKERS_FILE
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return [(int(row["start_ms"]), int(row["end_ms"]), str(row["speaker"]))
            for row in rows if {"start_ms", "end_ms", "speaker"} <= set(row)]


def read_named(path: pathlib.Path) -> dict[str, str]:
    """`A=Marco` lines from one file. Empty when it is absent or unreadable."""
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}
    named: dict[str, str] = {}
    for line in text.splitlines():
        letter, marker, name = line.partition("=")
        letter, name = letter.strip().upper(), name.strip()
        if marker and len(letter) == 1 and letter.isalpha() and name:
            named[letter] = name
    return named


def read_confirmed(recording_dir: pathlib.Path) -> dict[str, str]:
    """What you told `qn confirm`. Empty when you never did."""
    return read_named(recording_dir / CONFIRMED_FILE)


def read_matched(recording_dir: pathlib.Path) -> dict[str, str]:
    """What the voice roster recognised. Empty when it recognised nobody."""
    return read_named(recording_dir / MATCHED_FILE)


def names_for(recording_dir: pathlib.Path) -> dict[str, str]:
    """The name for each letter. You outrank the roster; Claude's guess stays
    in the frontmatter, because it reads words rather than hearing a voice."""
    return {**read_matched(recording_dir), **read_confirmed(recording_dir)}


def voice_at(voices: list[tuple[int, int, str]], start: int, end: int) -> str | None:
    """The voice group that overlaps this line the most, if any does.

    Overlap, not the nearest start: whisper's segment boundaries and the
    segmenter's are drawn by different models and never line up exactly.
    """
    best, longest = None, 0
    for voice_start, voice_end, speaker in voices:
        overlap = min(end, voice_end) - max(start, voice_start)
        if overlap > longest:
            best, longest = speaker, overlap
    return best


def read_segments(recording_dir: pathlib.Path) -> list[tuple[int, int, str, str]]:
    """Every segment from both tracks, as (start_ms, end_ms, speaker, text)."""
    segments: list[tuple[int, int, str, str]] = []
    voices = read_voices(recording_dir)
    named = names_for(recording_dir)
    for track, speaker in TRACKS:
        path = recording_dir / f"{track}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for segment in data.get("transcription", []):
            text = segment.get("text", "").strip()
            if text:
                offsets = segment["offsets"]
                start, end = offsets["from"], offsets["to"]
                label = speaker
                # Only the mixed track needs grouping. `Me` is its own file.
                if track == "them" and voices:
                    found = voice_at(voices, start, end)
                    if found is not None:
                        # A named voice becomes the person. An unnamed one
                        # stays a letter, which is a hint and says so.
                        label = named.get(found, f"{speaker} {found}")
                segments.append((start, end, label, text))
    return segments


def build_turns(segments: list[tuple[int, int, str, str]]) -> list[tuple[int, str, str]]:
    """Group segments into speaker turns, breaking on a change or a long pause.

    The gap is measured from the end of the previous segment. Measuring from
    its start made every segment longer than TURN_BREAK_MS look like a pause,
    and whisper emits segments up to 30 seconds long.
    """
    turns: list[list] = []
    previous_end: dict[str, int] = {}

    for start, end, speaker, text in sorted(segments, key=lambda s: s[0]):
        same_speaker = bool(turns) and turns[-1][1] == speaker
        paused = start - previous_end.get(speaker, start) >= TURN_BREAK_MS
        if same_speaker and not paused:
            turns[-1][2] += " " + text
        else:
            turns.append([start, speaker, text])
        previous_end[speaker] = max(end, start)

    return [(start, speaker, text) for start, speaker, text in turns]


def format_turns(turns: list[tuple[int, str, str]]) -> str:
    """Render turns as `[MM:SS] Speaker: text` lines."""
    lines = []
    for start, speaker, text in turns:
        seconds = start // 1000
        lines.append(f"[{seconds // 60:02d}:{seconds % 60:02d}] {speaker}: {text}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: merge.py <recording-dir>")
    print(format_turns(build_turns(read_segments(pathlib.Path(sys.argv[1])))))
