#!/usr/bin/env python3
"""Group the `them` track by voice, so Claude has a hint about who is who.

The `me` track needs none of this — it is a separate file, so your own lines
are already labelled. Only `them` is a mixdown of everyone else.

    python3 diarize.py <recording-dir>     writes speakers.json, voiceprints.json

`speakers.json` says when each group talked; `merge.py` reads it. The vectors
go in `voiceprints.json` instead, because a vector per time segment would make
that file a hundred times bigger for no reader.

The grouping is ours, not sherpa's. sherpa finds where each stretch of talking
starts and stops; we measure those stretches and group them here. Its own
clustering is discarded — see THRESHOLD.

This never names anybody. A wrong name is worse than "Them", so naming is left
to `voices.py`, which only repeats a name you confirmed.

Optional throughout: without `make diarize` this exits quietly.
"""

from __future__ import annotations

import json
import math
import operator
import os
import subprocess
import sys
import tempfile
import wave

# sherpa needs a clustering threshold to run, but we throw its groups away and
# build our own. It stays at the measured value so the segment boundaries this
# module was validated against do not move.
THRESHOLD = 0.75

# A span shorter than this gives a vector too noisy to group on. Short spans
# resemble each other more than they resemble their own speaker, so they form a
# junk group that swallows everybody's brief replies.
RELIABLE_SECONDS = 4.0

# Merge two groups while they are at least this alike, and the least a span may
# score to be given a voice at all. Across three hand-labelled meetings the
# closest two people scored 0.421 and one person at worst 0.532. See SPEC.md.
MERGE_SIMILARITY = 0.50

# Below this, pyannote's own segmenter is reporting noise rather than speech.
MIN_DURATION_ON = 0.5
MIN_DURATION_OFF = 0.5

# A group that never says much is not a colleague. Weighed twice: on the spans
# that formed the group, and again in `label` on the spans it kept, because
# `assign` can move spans out of it. Somebody who says ten seconds in an hour
# gets no letter — measured, and the reason Ravi is absent from one of the
# three meetings in SPEC.md.
MIN_SPEAKER_SECONDS = 20.0

# How much of a voice to store for the roster. The vector stops moving well
# before this, and `them.m4a` is a mixdown, so more audio mostly means more
# chances to swallow somebody talking over the top.
PRINT_SECONDS = 30.0

# Talking time needed before a voice is worth remembering. A 26-second sample
# cost about 0.03 on every later score, measured before the grouping was
# rebuilt. Below this a voice keeps its letter and its audio, but is never
# offered for naming.
MIN_PRINT_SECONDS = 60.0

# Both models run on one core unless told otherwise. Measured on twelve cores:
# four threads cut segmentation from 6.6s to 3.1s and the embedding pass from
# 12.8s to 4.1s. Eight threads bought nothing.
THREADS = 4

# What `to_wav` produces, and what both models expect.
SAMPLE_RATE = 16000

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

SEGMENTATION_MODEL = "segmentation.onnx"

# Its groups are discarded, but it is not spare: sherpa will not segment without
# an embedding model, and which one it gets moves the span boundaries. Handing
# it voiceprint.onnx gave 240 spans where this gives 318, and took twice as long.
EMBEDDING_MODEL = "embedding.onnx"

# Does both real jobs: grouping inside a meeting, and recognising the same
# person in the next one. See SPEC.md.
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


def segments_from(samples) -> list[tuple[float, float]]:
    """Where each stretch of talking starts and stops. Empty when unavailable."""
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
                model=segmentation), num_threads=THREADS),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=embedding, num_threads=THREADS),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=THRESHOLD),
        min_duration_on=MIN_DURATION_ON,
        min_duration_off=MIN_DURATION_OFF)
    if not config.validate():
        return []

    result = sherpa_onnx.OfflineSpeakerDiarization(config).process(
        samples).sort_by_start_time()
    return [(row.start, row.end) for row in result]


def unit(vector: list[float]) -> list[float]:
    """The vector at length one, so a dot product is a cosine."""
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else []


def similarity(left: list[float], right: list[float]) -> float:
    """Cosine of two unit vectors. Zero when either is missing."""
    return sum(map(operator.mul, left, right)) if left and right else 0.0


def centre(units: list[list[float]]) -> list[float]:
    """The average of several unit vectors, itself at length one."""
    return unit([sum(values) for values in zip(*units)]) if units else []


def cluster(vectors: list[list[float]], seconds: list[float]) -> list[list[int]]:
    """Group the spans by voice, busiest voice first.

    Only spans over RELIABLE_SECONDS get a say in who exists. Groups merge while
    their average likeness holds up, and a group that never says much is dropped
    — it is clustering noise, not a colleague.
    """
    members = {index: [index] for index, length in enumerate(seconds)
               if length >= RELIABLE_SECONDS and vectors[index]}
    units = {index: unit(vectors[index]) for index in members}

    def key(left, right):
        return (left, right) if left < right else (right, left)

    order = sorted(members)
    totals = {key(a, b): similarity(units[a], units[b])
              for position, a in enumerate(order) for b in order[position + 1:]}

    while len(members) > 1:
        best, closest = -2.0, None
        for pair, total in totals.items():
            left, right = pair
            average = total / (len(members[left]) * len(members[right]))
            if average > best:
                best, closest = average, pair
        if best < MERGE_SIMILARITY:
            break
        left, right = closest
        for other in members:
            if other not in closest:
                totals[key(left, other)] = (totals.pop(key(left, other))
                                            + totals.pop(key(right, other)))
        del totals[closest]
        members[left] += members[right]
        del members[right]

    def talked(group):
        return sum(seconds[index] for index in group)

    kept = [sorted(group) for group in members.values()
            if talked(group) >= MIN_SPEAKER_SECONDS]
    return sorted(kept, key=lambda group: (-talked(group), group[0]))


def assign(vectors: list[list[float]], groups: list[list[int]]) -> list[int | None]:
    """The group each span belongs to, or None when it resembles none of them.

    None is the honest answer for a two-second reply that could be anybody. Its
    line keeps the plain `Them` label rather than borrowing somebody's name.
    """
    centres = [centre([unit(vectors[index]) for index in group]) for group in groups]
    placed: list[int | None] = []
    for vector in vectors:
        point = unit(vector)
        scores = [similarity(point, middle) for middle in centres]
        best = max(scores, default=0.0)
        placed.append(scores.index(best) if best >= MERGE_SIMILARITY else None)
    return placed


def label(segments: list[tuple[float, float, int]]) -> list[dict]:
    """Turn group numbers into A, B, C, busiest voice first.

    A quiet group gets no letter at all. `cluster` weighs the same rule, but on
    the spans that decided the group; `assign` can move spans out of it, so the
    total is measured again on what was actually kept.
    """
    talking: dict[int, float] = {}
    for start, end, group in segments:
        talking[group] = talking.get(group, 0.0) + (end - start)

    ranked = [group for group, seconds in
              sorted(talking.items(), key=lambda item: (-item[1], item[0]))
              if seconds >= MIN_SPEAKER_SECONDS][:len(LETTERS)]
    letters = {group: LETTERS[position] for position, group in enumerate(ranked)}

    out = []
    for start, end, group in segments:
        if group in letters:
            out.append({"start_ms": int(start * 1000),
                        "end_ms": int(end * 1000),
                        "speaker": letters[group]})
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


def extractor():
    """The recognition model, ready to use. None when it was never fetched."""
    model = voiceprint_model()
    if model is None:
        return None
    try:
        import sherpa_onnx
    except ImportError:
        return None
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model, num_threads=THREADS)
    return sherpa_onnx.SpeakerEmbeddingExtractor(config) if config.validate() else None


def measure(engine, audio) -> list[float]:
    """One vector for one piece of audio. Empty when there is too little."""
    if audio.size == 0:
        return []
    stream = engine.create_stream()
    stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
    stream.input_finished()
    if not engine.is_ready(stream):
        return []
    return [float(value) for value in engine.compute(stream)]


def span_vectors(samples, spans: list[tuple[float, float]]) -> list[list[float]]:
    """One vector per span. Empty when the model was never fetched."""
    engine = extractor()
    if engine is None or not spans:
        return []
    return [measure(engine, samples[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
            for start, end in spans]


def voiceprints(samples, ranges: dict[str, list[tuple[int, int]]]) -> dict[str, list[float]]:
    """One vector per letter, from that letter's audio. Empty when unavailable."""
    engine = extractor()
    if engine is None or not ranges:
        return {}
    try:
        import numpy
    except ImportError:
        return {}

    prints: dict[str, list[float]] = {}
    for letter, spans in sorted(ranges.items()):
        pieces = [samples[int(start * SAMPLE_RATE / 1000):int(end * SAMPLE_RATE / 1000)]
                  for start, end in spans]
        audio = numpy.concatenate(pieces) if pieces else numpy.empty(0, dtype="float32")
        vector = measure(engine, audio)
        if vector:
            prints[letter] = vector
    return prints


def run(recording_dir: str) -> list[dict]:
    """Diarize `them.m4a`. Returns the segments; failed prints leave them."""
    audio = os.path.join(recording_dir, "them.m4a")
    if not os.path.exists(audio):
        return []

    samples = load_samples(audio)
    if samples is None:
        return []

    spans = segments_from(samples)
    vectors = span_vectors(samples, spans)
    if not vectors:
        return []

    groups = cluster(vectors, [end - start for start, end in spans])
    labelled = label([(start, end, group)
                      for (start, end), group in zip(spans, assign(vectors, groups))
                      if group is not None])
    if not labelled:
        return []

    with open(os.path.join(recording_dir, SPEAKERS_FILE), "w", encoding="utf-8") as handle:
        json.dump(labelled, handle)

    # Only the voices worth remembering get a print. The letters of the rest
    # still read in the transcript, and `qn play` still plays them.
    strong = {letter: ranges for letter, ranges in print_ranges(labelled).items()
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
