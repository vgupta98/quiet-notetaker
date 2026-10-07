"""Tests for cutting a track into stretches of talking.

The point of the module is one promise: a line cannot land in the transcript
outside the stretch it was said in. Before it, whisper removed the silences
itself, and a sentence said after ten quiet minutes started where the sentence
before them had ended.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib"), os.path.join(ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import json
import pathlib
import struct
import tempfile
import unittest
import wave

from stretches import GAP_MS, MIN_ALONE_MS, cut, join, parse_spans, stretches

# What `whisper-vad-speech-segments -np` prints. The numbers are hundredths of
# a second.
DETECTOR = """
Detected 3 speech segments:
Speech segment 0: start = 67.00, end = 102.00
Speech segment 1: start = 2570.00, end = 2608.00
Speech segment 2: start = 63230.50, end = 64660.00
"""

RATE = 16000


def write_wav(path, seconds):
    """A wav whose every sample holds the second it belongs to."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        for second in range(seconds):
            handle.writeframes(struct.pack("<h", second) * RATE)


class ReadTheDetector(unittest.TestCase):
    def test_reads_milliseconds_and_nothing_else(self):
        self.assertEqual(parse_spans(DETECTOR),
                         [(670, 1020), (25700, 26080), (632305, 646600)])
        self.assertEqual(parse_spans("the detector said nothing"), [])


class FindStretches(unittest.TestCase):
    def test_a_short_pause_stays_inside_a_stretch(self):
        found = stretches([(0, 2000), (2000 + GAP_MS - 1, 9000)])
        self.assertEqual(found, [(0, 9000)])

    def test_a_long_silence_ends_a_stretch(self):
        found = stretches([(0, 2000), (2000 + GAP_MS, 12000)])
        self.assertEqual(found, [(0, 2000), (2000 + GAP_MS, 12000)])

    def test_a_blip_is_dropped_only_when_it_stands_alone(self):
        blip = MIN_ALONE_MS - 1
        # Near other speech it is part of the sentence around it.
        self.assertEqual(stretches([(0, 3000), (4000, 4000 + blip)]), [(0, 4000 + blip)])
        # Alone, whisper invents words for it, so it is not transcribed.
        alone = 3000 + GAP_MS
        self.assertEqual(stretches([(0, 3000), (alone, alone + blip)]), [(0, 3000)])

    def test_the_detector_need_not_be_in_order(self):
        self.assertEqual(stretches([(20000, 23000), (0, 2000)]), [(0, 2000), (20000, 23000)])

    def test_no_speech_is_no_stretches(self):
        self.assertEqual(stretches([]), [])


class CutAndJoin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.parts = self.dir / "parts"
        self.parts.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def heard(self, name, text):
        """Stand in for whisper: it wrote this beside the stretch it was given."""
        (self.parts / f"{name}.json").write_text(json.dumps({"transcription": [
            {"offsets": {"from": 500, "to": 1500}, "text": text}]}))

    def test_a_line_keeps_the_time_of_the_stretch_it_was_said_in(self):
        write_wav(self.dir / "me.wav", 20)
        cut(self.dir / "me.wav", [(2000, 4000), (15000, 18000)], self.parts)

        names = sorted(path.name for path in self.parts.iterdir())
        self.assertEqual(names, ["000002000.wav", "000015000.wav"])
        with wave.open(str(self.parts / "000015000.wav")) as part:
            self.assertEqual(part.getnframes(), 3 * RATE)
            # The audio is the track's own, from the fifteenth second on.
            self.assertEqual(struct.unpack("<h", part.readframes(1))[0], 15)

        # Thirteen quiet seconds lie between the two. The second line must
        # start after them, not where the first one ended.
        self.heard("000002000.wav", " first")
        self.heard("000015000.wav", " second")
        self.assertEqual(join(self.parts)["transcription"], [
            {"offsets": {"from": 2500, "to": 3500}, "text": " first"},
            {"offsets": {"from": 15500, "to": 16500}, "text": " second"},
        ])

    def test_a_stretch_past_the_end_of_the_audio_writes_no_file(self):
        write_wav(self.dir / "me.wav", 2)
        cut(self.dir / "me.wav", [(5000, 8000)], self.parts)
        self.assertEqual(list(self.parts.iterdir()), [])

    def test_nothing_heard_is_an_empty_transcription(self):
        self.assertEqual(join(self.parts), {"transcription": []})


if __name__ == "__main__":
    unittest.main()
