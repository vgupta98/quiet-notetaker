"""Cut a track where nobody talks, so whisper hears each stretch of talking alone.

whisper can take the silences out by itself, with `--vad`. It then hears the
talking as one unbroken stream, and a sentence said after ten quiet minutes is
joined to the one before them. The joined line starts where the earlier
sentence ended, so it lands in the transcript ten minutes before it was said.

So the cutting is done here. Each stretch becomes a file of its own, whisper
hears it alone, and its times are shifted by where the stretch began. A line
cannot land outside the stretch it was said in.

Measured on six real tracks, against a second transcript whose times are known.
Before, 86 of 1,395 lines sat more than ten seconds from where they were said,
and the worst sat 581 seconds early. After, 1 of 1,763 did, by 19 seconds. The
words did not suffer: 26,471 against 25,661.

Usage: whisper-vad-speech-segments ... | python3 stretches.py cut <wav> <dir>
       python3 stretches.py join <dir>   -> one whisper-shaped JSON on stdout
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import wave

# A silence this long ends a stretch. Shorter ones stay inside it, as real
# audio, which is what whisper is built to hear. The numbers above are for
# this value. Cutting at every one-second pause was measured too: whisper
# invented three times as many lines, 31 against 10, because far more of the
# pieces were under a second.
GAP_MS = 5_000

# A stretch shorter than this, with nothing near it, is not transcribed.
# Measured on one real microphone track: 17 of them, and whisper invented the
# words for 7, such as "Thank you." The longest real thing in the other ten
# was four words.
MIN_ALONE_MS = 1_000

_SPAN = re.compile(r"start = ([\d.]+), end = ([\d.]+)")


def parse_spans(vad_output: str) -> list[tuple[int, int]]:
    """Where the voice detector heard speech, in milliseconds.

    `whisper-vad-speech-segments` prints hundredths of a second.
    """
    return [(round(float(start) * 10), round(float(end) * 10))
            for start, end in _SPAN.findall(vad_output)]


def stretches(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Join speech that is close together, and drop a blip that stands alone."""
    joined: list[list[int]] = []
    for start, end in sorted(spans):
        if joined and start - joined[-1][1] < GAP_MS:
            joined[-1][1] = max(joined[-1][1], end)
        else:
            joined.append([start, end])
    return [(start, end) for start, end in joined if end - start >= MIN_ALONE_MS]


def cut(wav: pathlib.Path, found: list[tuple[int, int]], directory: pathlib.Path) -> None:
    """Write each stretch as its own wav, named after the moment it starts."""
    with wave.open(str(wav)) as source:
        rate = source.getframerate()
        for start, end in found:
            source.setpos(min(start * rate // 1000, source.getnframes()))
            frames = source.readframes((end - start) * rate // 1000)
            if not frames:
                continue
            with wave.open(str(directory / f"{start:09d}.wav"), "wb") as part:
                part.setparams(source.getparams())
                part.writeframes(frames)


def join(directory: pathlib.Path) -> dict:
    """Every stretch's segments as one transcription, on the track's own clock."""
    segments = []
    for path in sorted(directory.glob("*.wav.json")):
        began = int(path.name.split(".")[0])
        for segment in json.loads(path.read_text()).get("transcription", []):
            offsets = segment["offsets"]
            segments.append({
                "offsets": {"from": began + offsets["from"], "to": began + offsets["to"]},
                "text": segment.get("text", ""),
            })
    return {"transcription": segments}


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "cut":
        cut(pathlib.Path(sys.argv[2]), stretches(parse_spans(sys.stdin.read())),
            pathlib.Path(sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[1] == "join":
        print(json.dumps(join(pathlib.Path(sys.argv[2]))))
    else:
        sys.exit("usage: stretches.py cut <wav> <dir> | stretches.py join <dir>")
