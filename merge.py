"""Merge the two whisper JSON files into one conversation, in the order it was said."""
import json
import pathlib
import sys

work = pathlib.Path(sys.argv[1])

segments = []
for track, speaker in (("them", "Them"), ("me", "Me")):
    path = work / f"{track}.json"
    if not path.exists():
        continue
    data = json.loads(path.read_text())
    for segment in data.get("transcription", []):
        text = segment.get("text", "").strip()
        if text:
            segments.append((segment["offsets"]["from"], speaker, text))

segments.sort(key=lambda s: s[0])

# One block per speaker turn, rather than one line per whisper segment.
turns = []
for start, speaker, text in segments:
    if turns and turns[-1][1] == speaker:
        turns[-1][2] += " " + text
    else:
        turns.append([start, speaker, text])

for start, speaker, text in turns:
    seconds = start // 1000
    print(f"[{seconds // 60:02d}:{seconds % 60:02d}] {speaker}: {text}")
