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


def advise(report: dict) -> list[tuple[str, str]]:
    """Read a health report as a microphone test rather than as a meeting.

    The same numbers mean different things here. A meeting whose `them` track
    is silent is broken. A microphone test whose `them` track is silent is
    fine: nothing was playing. So `judge()` cannot answer this, and reusing it
    would tell the user their setup is broken when it is working.

    Returns (verdict, message) pairs, where verdict is ok, warn or fail.
    """
    tracks = report.get("tracks", {})
    out: list[tuple[str, str]] = []

    mic = tracks.get("me") or {}
    if mic.get("seconds") is None:
        out.append(("fail", "no microphone audio — grant Microphone access, then try again"))
    elif mic.get("mean_db") is None:
        out.append(("fail", "the microphone track was recorded but cannot be read"))
    elif mic["mean_db"] < SILENT_MEAN_DB:
        out.append(("fail", f"the microphone heard nothing ({mic['mean_db']:.0f} dB) — "
                            "check it is not muted, and that the right input is selected"))
    elif mic["mean_db"] < QUIET_MEAN_DB:
        out.append(("warn", f"the microphone is very quiet ({mic['mean_db']:.0f} dB) — "
                            "move closer, or raise the input level"))
    elif mic.get("peak_db") is not None and mic["peak_db"] >= CLIPPING_PEAK_DB:
        out.append(("warn", f"the microphone is clipping ({mic['peak_db']:.0f} dB peak) — "
                            "lower the input level"))
    else:
        out.append(("ok", f"your microphone works ({mic['mean_db']:.0f} dB average)"))

    system = tracks.get("them") or {}
    if system.get("seconds") is None:
        out.append(("fail", "no system audio — grant Screen Recording access, "
                            "then quit and reopen the app you run qn from"))
    elif system.get("mean_db") is None:
        out.append(("fail", "the system audio track was recorded but cannot be read"))
    elif system["mean_db"] < SILENT_MEAN_DB:
        # The file exists and has length, so the capture itself worked.
        out.append(("ok", "system audio is being captured — nothing was playing, "
                          "so play something during the test to check it fully"))
    else:
        out.append(("ok", f"system audio works ({system['mean_db']:.0f} dB average)"))

    return out


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
        sys.exit("usage: health.py <recording-dir> [--advise]")
    _report = check(pathlib.Path(sys.argv[1]))
    if "--advise" in sys.argv:
        # One `verdict<TAB>message` per line, for `qn doctor --mic` to colour.
        for _verdict, _message in advise(_report):
            print(f"{_verdict}\t{_message}")
    else:
        print(json.dumps(_report))
