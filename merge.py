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

# One speaker talking for two minutes is one turn to whisper and a wall of text
# to a reader. A pause this long is where a paragraph should break.
TURN_BREAK_MS = 8_000


def read_segments(recording_dir: pathlib.Path) -> list[tuple[int, str, str]]:
    """Every transcribed segment from both tracks, as (start_ms, speaker, text)."""
    segments: list[tuple[int, str, str]] = []
    for track, speaker in TRACKS:
        path = recording_dir / f"{track}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for segment in data.get("transcription", []):
            text = segment.get("text", "").strip()
            if text:
                segments.append((segment["offsets"]["from"], speaker, text))
    return segments


def build_turns(segments: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    """Group segments into speaker turns, breaking on a change or a long pause."""
    turns: list[list] = []
    previous_end: dict[str, int] = {}

    for start, speaker, text in sorted(segments, key=lambda s: s[0]):
        same_speaker = bool(turns) and turns[-1][1] == speaker
        paused = start - previous_end.get(speaker, start) >= TURN_BREAK_MS
        if same_speaker and not paused:
            turns[-1][2] += " " + text
        else:
            turns.append([start, speaker, text])
        previous_end[speaker] = start

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
