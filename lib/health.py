"""Judge whether a recording is usable, and say so plainly.

Every tool in this space fails silently: it records one track instead of two,
or captures 40 minutes of nothing, and hands you confident notes built on air.
This module exists so that never happens quietly.

Usage: python3 health.py <recording-dir>   -> JSON on stdout
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

TRACKS = (("them", "everyone else"), ("me", "your microphone"))

# Thresholds in dBFS, calibrated against real recordings. A genuine but quiet
# mic track measures about -33 dB mean / -3 dB peak, so "silent" has to sit
# well below that to avoid crying wolf.
SILENT_MEAN_DB = -50.0
QUIET_MEAN_DB = -40.0
CLIPPING_PEAK_DB = -0.5
MIN_USEFUL_SECONDS = 5.0
# Tracks are recorded from one stream, so a large length gap means one of them
# stopped early.
MAX_LENGTH_GAP = 0.25

_MEAN = re.compile(r"mean_volume:\s*(-?[\d.]+) dB")
_PEAK = re.compile(r"max_volume:\s*(-?[\d.]+) dB")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")


class Measurement:
    """What ffmpeg could tell us about one audio file."""

    def __init__(
        self,
        seconds: float | None,
        mean_db: float | None,
        peak_db: float | None,
        *,
        on_disk: bool = False,
        readable: bool = True,
    ):
        self.seconds = seconds
        self.mean_db = mean_db
        self.peak_db = peak_db
        # A file can exist and still be unreadable — an interrupted recording
        # has no moov atom. Calling that "missing" sends people looking for a
        # file that is sitting right there with an hour of audio in it.
        self.on_disk = on_disk
        self.readable = readable

    @property
    def present(self) -> bool:
        return self.seconds is not None


def measure(path: pathlib.Path) -> Measurement:
    """Read length and loudness out of one audio file."""
    if not path.exists() or path.stat().st_size == 0:
        return Measurement(None, None, None)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af",
             "volumedetect", "-f", "null", "-"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        # No ffmpeg. Say so once, rather than dying with a traceback.
        return Measurement(None, None, None, on_disk=True, readable=False)

    found = _parse(proc.stderr)
    found.on_disk = True
    found.readable = proc.returncode == 0 and found.seconds is not None
    return found


def _parse(ffmpeg_stderr: str) -> Measurement:
    """Pull the numbers out of ffmpeg's log. Absent values stay None."""
    seconds = None
    if (found := _DURATION.search(ffmpeg_stderr)) is not None:
        hours, minutes, secs = found.groups()
        seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)
    mean = float(found.group(1)) if (found := _MEAN.search(ffmpeg_stderr)) else None
    peak = float(found.group(1)) if (found := _PEAK.search(ffmpeg_stderr)) else None
    return Measurement(seconds, mean, peak)


def judge(measurements: dict[str, Measurement]) -> list[str]:
    """Turn measurements into warnings a person can act on."""
    warnings: list[str] = []

    for track, who in TRACKS:
        found = measurements.get(track)
        if found is None or not found.present:
            if found is not None and found.on_disk:
                warnings.append(
                    f"the {track} track is damaged and cannot be read — "
                    "the recording was probably interrupted"
                )
            else:
                warnings.append(f"no audio from {who} — the {track} track is missing")
            continue
        if found.seconds is not None and found.seconds < MIN_USEFUL_SECONDS:
            warnings.append(f"the {track} track is only {found.seconds:.0f}s long")
        if found.mean_db is not None:
            if found.mean_db < SILENT_MEAN_DB:
                warnings.append(f"heard nothing from {who} — the {track} track is silent")
            elif found.mean_db < QUIET_MEAN_DB:
                warnings.append(f"{who} was very quiet ({found.mean_db:.0f} dB) — expect a poor transcript")
        if found.peak_db is not None and found.peak_db >= CLIPPING_PEAK_DB:
            warnings.append(f"the {track} track is clipping — the transcript will suffer")

    lengths = [m.seconds for m in measurements.values() if m.present and m.seconds]
    if len(lengths) == 2:
        longest = max(lengths)
        if longest > 0 and (longest - min(lengths)) / longest > MAX_LENGTH_GAP:
            warnings.append("one track stopped well before the other")

    return warnings


def check(recording_dir: pathlib.Path) -> dict:
    """Full health report for one recording."""
    measurements = {track: measure(recording_dir / f"{track}.m4a") for track, _ in TRACKS}
    warnings = judge(measurements)
    return {
        "capture": "warn" if warnings else "ok",
        "warnings": warnings,
        "tracks": {
            track: {"seconds": m.seconds, "mean_db": m.mean_db, "peak_db": m.peak_db}
            for track, m in measurements.items()
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: health.py <recording-dir>")
    print(json.dumps(check(pathlib.Path(sys.argv[1]))))
