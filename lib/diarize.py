#!/usr/bin/env python3
"""Group the `them` track by voice, so Claude has a hint about who is who.

The `me` track needs none of this — it is a separate file, so your own lines
are already labelled. Only `them` is a mixdown of everyone else.

    python3 diarize.py <recording-dir>     writes <dir>/speakers.json

What this is NOT
----------------
This does not name anybody, and it is deliberately not allowed to. Measured on
a real 26-minute standup, the clustering put a question and its answer in the
same group — two people, one label. Writing "Marco:" onto a line that was
Lena would corrupt the action items, and a wrong name is worse than "Them".

So the output is `Them A`, `Them B`. `prompt.md` tells Claude these are a hint
from voice grouping and may be wrong, and that context wins when they disagree.
Claude can overrule a bad cluster. An automatic label cannot.

Everything here is optional. Without `make diarize` the models are absent, this
exits quietly, and the transcript reads exactly as it does today.
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

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

SEGMENTATION_MODEL = "segmentation.onnx"
EMBEDDING_MODEL = "embedding.onnx"


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


def segments_for(audio_path: str) -> list[tuple[float, float, int]]:
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

    with tempfile.TemporaryDirectory() as scratch:
        wav = os.path.join(scratch, "them.wav")
        if not to_wav(audio_path, wav):
            return []
        result = sherpa_onnx.OfflineSpeakerDiarization(config).process(
            read_wav(wav)).sort_by_start_time()

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


def run(recording_dir: str) -> list[dict]:
    """Diarize `them.m4a` and write speakers.json. Returns what it wrote."""
    audio = os.path.join(recording_dir, "them.m4a")
    if not os.path.exists(audio):
        return []

    labelled = label(segments_for(audio))
    if not labelled:
        return []

    with open(os.path.join(recording_dir, "speakers.json"), "w", encoding="utf-8") as handle:
        json.dump(labelled, handle)
    return labelled


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: diarize.py <recording-dir>")
    written = run(sys.argv[1])
    voices = len({row["speaker"] for row in written})
    print(f"{voices} voices over {len(written)} segments" if written else "no diarization")
