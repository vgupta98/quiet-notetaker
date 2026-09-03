#!/usr/bin/env python3
"""Group the `them` track by voice, so Claude has a hint about who is who.

The `me` track needs none of this — it is a separate file, so your own lines
are already labelled. Only `them` is a mixdown of everyone else.

    python3 diarize.py <recording-dir>     writes speakers.json, voiceprints.json

`speakers.json` says when each group talked; `merge.py` reads it. The vectors
go in `voiceprints.json` instead, because a vector per time segment would make
that file a hundred times bigger for no reader.

This never names anybody. Measured on a real standup, the clustering put a
question and its answer in one group. A wrong name is worse than "Them", so
naming is left to `voices.py`, which only repeats a name you confirmed.

Optional throughout: without `make diarize` this exits quietly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import wave

# From a threshold sweep on a real meeting. 0.6 found 25 speakers in a call
# with five; 0.9 merged several people into one 11-minute block. 0.75 gave five
# groups with a believable share of the talking each.
THRESHOLD = 0.75

# Below this, pyannote's own segmenter is reporting noise rather than speech.
MIN_DURATION_ON = 0.5
MIN_DURATION_OFF = 0.5

# A cluster that never says much is clustering noise, not a colleague. The same
# sweep produced twenty groups holding one short line each.
MIN_SPEAKER_SECONDS = 20.0

# More than this many voices in one call and the labels stop helping a reader.
MAX_SPEAKERS = 6

# How much of a voice to feed the embedding model. The vector stops moving
# well before this, and `them.m4a` is a mixdown, so more audio mostly means
# more chances to swallow somebody talking over the top.
PRINT_SECONDS = 30.0

# Talking time needed before a voice is worth remembering. A 26-second sample
# cost about 0.03 on every later score. Below this a voice keeps its letter
# and its audio, but is never offered for naming.
MIN_PRINT_SECONDS = 60.0

# What `to_wav` produces, and what both models expect.
SAMPLE_RATE = 16000

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

SEGMENTATION_MODEL = "segmentation.onnx"
EMBEDDING_MODEL = "embedding.onnx"

# Recognition needs its own model. `embedding.onnx` groups well but cannot
# tell colleagues apart across meetings: it scored two different people 0.931
# against one person's 0.923. Separate models also keep the clustering
# threshold valid when one of them changes. See SPEC.md.
VOICEPRINT_MODEL = "voiceprint.onnx"

SPEAKERS_FILE = "speakers.json"
PRINTS_FILE = "voiceprints.json"


def models_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


def model_paths() -> tuple[str, str] | None:
    """Both model files, or None when diarization was never set up."""
    segmentation = os.path.join(models_dir(), SEGMENTATION_MODEL)
    embedding = os.path.join(models_dir(), EMBEDDING_MODEL)
    if os.path.exists(segmentation) and os.path.exists(embedding):
        return segmentation, embedding
    return None


def to_wav(source: str, destination: str) -> bool:
    """16 kHz mono, which is what the models expect."""
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", source,
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", destination],
            check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return os.path.exists(destination) and os.path.getsize(destination) > 0


def read_wav(path: str):
    """Mono float samples in [-1, 1). numpy is a sherpa-onnx dependency."""
    import numpy

    with wave.open(path) as handle:
        raw = handle.readframes(handle.getnframes())
    return numpy.frombuffer(raw, dtype=numpy.int16).astype(numpy.float32) / 32768.0


def load_samples(audio_path: str):
    """The track as 16 kHz mono. Decoded once, used by both passes."""
    with tempfile.TemporaryDirectory() as scratch:
        wav = os.path.join(scratch, "them.wav")
        if not to_wav(audio_path, wav):
            return None
        return read_wav(wav)


def segments_from(samples) -> list[tuple[float, float, int]]:
    """Raw (start, end, cluster) from the models. Empty when unavailable."""
    paths = model_paths()
    if paths is None:
        return []
    segmentation, embedding = paths

    try:
        import sherpa_onnx
    except ImportError:
        return []

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=segmentation)),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=embedding),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=THRESHOLD),
        min_duration_on=MIN_DURATION_ON,
        min_duration_off=MIN_DURATION_OFF)
    if not config.validate():
        return []

    result = sherpa_onnx.OfflineSpeakerDiarization(config).process(
        samples).sort_by_start_time()
    return [(row.start, row.end, row.speaker) for row in result]


def label(segments: list[tuple[float, float, int]]) -> list[dict]:
    """Turn cluster numbers into A, B, C, busiest voice first.

    A quiet cluster gets no letter at all. Its lines stay plain `Them`, which
    is the honest answer when there is not enough voice to group on.
    """
    talking: dict[int, float] = {}
    for start, end, cluster in segments:
        talking[cluster] = talking.get(cluster, 0.0) + (end - start)

    ranked = [cluster for cluster, seconds in
              sorted(talking.items(), key=lambda item: (-item[1], item[0]))
              if seconds >= MIN_SPEAKER_SECONDS][:MAX_SPEAKERS]
    letters = {cluster: LETTERS[position] for position, cluster in enumerate(ranked)}

    out = []
    for start, end, cluster in segments:
        if cluster in letters:
            out.append({"start_ms": int(start * 1000),
                        "end_ms": int(end * 1000),
                        "speaker": letters[cluster]})
    return out


def talking(labelled: list[dict]) -> dict[str, float]:
    """Seconds each voice talked. One reader, so one definition of the number."""
    totals: dict[str, float] = {}
    for row in labelled:
        letter = str(row["speaker"])
        totals[letter] = totals.get(letter, 0.0) + (row["end_ms"] - row["start_ms"]) / 1000
    return totals


def worth_remembering(labelled: list[dict]) -> set[str]:
    """The voices that talked long enough to be recognised later."""
    return {letter for letter, seconds in talking(labelled).items()
            if seconds >= MIN_PRINT_SECONDS}


def print_ranges(labelled: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """Audio to print each voice from: longest stretches first, up to
    PRINT_SECONDS. Short answers are where a mixdown holds two people."""
    by_letter: dict[str, list[dict]] = {}
    for row in labelled:
        by_letter.setdefault(row["speaker"], []).append(row)

    ranges: dict[str, list[tuple[int, int]]] = {}
    for letter, rows in by_letter.items():
        longest = sorted(rows, key=lambda row: (row["start_ms"] - row["end_ms"],
                                                row["start_ms"]))
        chosen, budget = [], PRINT_SECONDS * 1000
        for row in longest:
            if budget <= 0:
                break
            span = min(row["end_ms"] - row["start_ms"], int(budget))
            chosen.append((row["start_ms"], row["start_ms"] + span))
            budget -= span
        ranges[letter] = sorted(chosen)
    return ranges


def voiceprint_model() -> str | None:
    """The recognition model, or None. Without it, grouping still works."""
    path = os.path.join(models_dir(), VOICEPRINT_MODEL)
    return path if os.path.exists(path) else None


def voiceprints(samples, ranges: dict[str, list[tuple[int, int]]]) -> dict[str, list[float]]:
    """One vector per letter, from that letter's audio. Empty when unavailable."""
    model = voiceprint_model()
    if model is None or not ranges:
        return {}
    try:
        import numpy
        import sherpa_onnx
    except ImportError:
        return {}

    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model)
    if not config.validate():
        return {}
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    prints: dict[str, list[float]] = {}
    for letter, spans in sorted(ranges.items()):
        pieces = [samples[int(start * SAMPLE_RATE / 1000):int(end * SAMPLE_RATE / 1000)]
                  for start, end in spans]
        audio = numpy.concatenate(pieces) if pieces else numpy.empty(0, dtype="float32")
        if audio.size == 0:
            continue
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
        stream.input_finished()
        if extractor.is_ready(stream):
            prints[letter] = [float(value) for value in extractor.compute(stream)]
    return prints


def run(recording_dir: str) -> list[dict]:
    """Diarize `them.m4a`. Returns the segments; failed prints leave them."""
    audio = os.path.join(recording_dir, "them.m4a")
    if not os.path.exists(audio):
        return []

    samples = load_samples(audio)
    if samples is None:
        return []

    labelled = label(segments_from(samples))
    if not labelled:
        return []

    with open(os.path.join(recording_dir, SPEAKERS_FILE), "w", encoding="utf-8") as handle:
        json.dump(labelled, handle)

    # Only the voices worth remembering get a print. The letters of the rest
    # still read in the transcript, and `qn play` still plays them.
    strong = {letter: spans for letter, spans in print_ranges(labelled).items()
              if letter in worth_remembering(labelled)}
    prints = voiceprints(samples, strong)
    if prints:
        with open(os.path.join(recording_dir, PRINTS_FILE), "w", encoding="utf-8") as handle:
            json.dump(prints, handle)
    return labelled


def play_spans(recording_dir: str, letter: str) -> list[tuple[float, float]]:
    """The seconds the print was built from, so you hear what the roster heard."""
    ranges = print_ranges(read_speakers(recording_dir))
    return [(start / 1000, end / 1000)
            for start, end in ranges.get(str(letter).strip().upper(), [])]


def read_speakers(recording_dir: str) -> list[dict]:
    """The voice groups of one meeting. Empty when it was never diarized."""
    try:
        with open(os.path.join(recording_dir, SPEAKERS_FILE), encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)
            and {"start_ms", "end_ms", "speaker"} <= set(row)]


def read_prints(recording_dir: str) -> dict[str, list[float]]:
    """The voiceprints of one meeting. Empty when it was never diarized."""
    try:
        with open(os.path.join(recording_dir, PRINTS_FILE), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {letter: [float(value) for value in vector]
            for letter, vector in data.items()
            if isinstance(letter, str) and isinstance(vector, list) and vector
            and all(isinstance(value, (int, float)) for value in vector)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: diarize.py <recording-dir> [--spans <letter>]")

    # `qn play <id> <letter>` asks for the spans and does the playing itself.
    # Stdlib only, so it runs on the system python without the venv.
    if "--spans" in sys.argv:
        position = sys.argv.index("--spans")
        wanted = sys.argv[position + 1] if position + 1 < len(sys.argv) else ""
        for start, end in play_spans(sys.argv[1], wanted):
            print(f"{start:.3f} {end:.3f}")
        sys.exit(0)

    written = run(sys.argv[1])
    speakers = len({row["speaker"] for row in written})
    printed = len(read_prints(sys.argv[1]))
    print(f"{speakers} voices over {len(written)} segments, {printed} voiceprints"
          if written else "no diarization")
