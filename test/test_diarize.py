#!/usr/bin/env python3
"""Tests for diarize.py — grouping the `them` track by voice.

The models are optional and large, so nothing here loads them. These cover the
decisions the module makes about the segments it is handed, which is where the
judgement lives.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib"), os.path.join(ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import json
import shutil
import tempfile
import unittest

import diarize
import merge
import pathlib


def talk(cluster, seconds, at=0.0):
    """One segment of `seconds` for `cluster`, starting at `at`."""
    return (at, at + seconds, cluster)


class Labelling(unittest.TestCase):
    def test_the_busiest_voice_is_a(self):
        rows = diarize.label([talk(7, 100, 0), talk(3, 200, 200)])
        by_cluster = {row["start_ms"]: row["speaker"] for row in rows}
        self.assertEqual(by_cluster[200_000], "A")   # the 200-second voice
        self.assertEqual(by_cluster[0], "B")

    def test_a_quiet_cluster_gets_no_letter(self):
        """Twenty groups holding one short line each is clustering noise."""
        rows = diarize.label([talk(1, 100), talk(2, 5.0, 200)])
        self.assertEqual({row["speaker"] for row in rows}, {"A"})

    def test_the_threshold_is_the_stated_one(self):
        just_under = diarize.MIN_SPEAKER_SECONDS - 0.1
        self.assertEqual(diarize.label([talk(1, just_under)]), [])
        self.assertEqual(len(diarize.label([talk(1, diarize.MIN_SPEAKER_SECONDS)])), 1)

    def test_too_many_voices_are_capped(self):
        segments = [talk(n, 100 - n, at=n * 200) for n in range(diarize.MAX_SPEAKERS + 4)]
        letters = {row["speaker"] for row in diarize.label(segments)}
        self.assertEqual(len(letters), diarize.MAX_SPEAKERS)

    def test_no_segments_means_no_labels(self):
        self.assertEqual(diarize.label([]), [])

    def test_times_become_milliseconds(self):
        rows = diarize.label([(1.5, 61.5, 0)])
        self.assertEqual((rows[0]["start_ms"], rows[0]["end_ms"]), (1500, 61500))


class WithoutModels(unittest.TestCase):
    """Diarization is optional. Its absence must change nothing."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qn-diar-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_a_missing_recording_is_not_an_error(self):
        self.assertEqual(diarize.run(self.dir), [])

    def test_no_speakers_file_is_written_when_there_is_nothing_to_write(self):
        diarize.run(self.dir)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "speakers.json")))


class MergeReadsTheHint(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qn-merge-diar-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = pathlib.Path(self.dir)

    def write_track(self, track, segments):
        rows = [{"offsets": {"from": a, "to": b}, "text": text} for a, b, text in segments]
        (self.path / f"{track}.json").write_text(json.dumps({"transcription": rows}))

    def write_voices(self, rows):
        (self.path / "speakers.json").write_text(json.dumps(rows))

    def test_them_lines_carry_a_letter(self):
        self.write_track("them", [(0, 2000, "morning all"), (10_000, 12_000, "no blockers")])
        self.write_voices([{"start_ms": 0, "end_ms": 3000, "speaker": "A"},
                           {"start_ms": 9000, "end_ms": 13_000, "speaker": "B"}])
        speakers = [row[2] for row in merge.read_segments(self.path)]
        self.assertEqual(speakers, ["Them A", "Them B"])

    def test_the_me_track_is_never_relabelled(self):
        """`me` is already its own file. Grouping it would be nonsense."""
        self.write_track("me", [(0, 2000, "morning")])
        self.write_voices([{"start_ms": 0, "end_ms": 3000, "speaker": "A"}])
        self.assertEqual([row[2] for row in merge.read_segments(self.path)], ["Me"])

    def test_a_line_outside_every_group_stays_plain(self):
        self.write_track("them", [(60_000, 62_000, "who was that")])
        self.write_voices([{"start_ms": 0, "end_ms": 3000, "speaker": "A"}])
        self.assertEqual([row[2] for row in merge.read_segments(self.path)], ["Them"])

    def test_the_longest_overlap_wins(self):
        """Whisper and the segmenter draw boundaries with different models."""
        self.write_track("them", [(1000, 5000, "a longer line")])
        self.write_voices([{"start_ms": 0, "end_ms": 1500, "speaker": "A"},
                           {"start_ms": 1500, "end_ms": 6000, "speaker": "B"}])
        self.assertEqual([row[2] for row in merge.read_segments(self.path)], ["Them B"])

    def test_no_speakers_file_reads_exactly_as_before(self):
        self.write_track("them", [(0, 2000, "morning all")])
        self.assertEqual([row[2] for row in merge.read_segments(self.path)], ["Them"])

    def test_a_corrupt_speakers_file_is_ignored(self):
        """A broken hint must never cost you the transcript."""
        self.write_track("them", [(0, 2000, "morning all")])
        (self.path / "speakers.json").write_text("{not json")
        self.assertEqual([row[2] for row in merge.read_segments(self.path)], ["Them"])

    def test_rows_missing_fields_are_skipped(self):
        self.write_track("them", [(0, 2000, "morning all")])
        self.write_voices([{"start_ms": 0, "speaker": "A"}])
        self.assertEqual([row[2] for row in merge.read_segments(self.path)], ["Them"])

    def test_two_voices_do_not_merge_into_one_turn(self):
        """Them A and Them B are different speakers, so the turn must break."""
        self.write_track("them", [(0, 2000, "shall I start"), (2100, 4000, "yes go ahead")])
        self.write_voices([{"start_ms": 0, "end_ms": 2050, "speaker": "A"},
                           {"start_ms": 2050, "end_ms": 5000, "speaker": "B"}])
        turns = merge.build_turns(merge.read_segments(self.path))
        self.assertEqual([turn[1] for turn in turns], ["Them A", "Them B"])


class Confirmations(unittest.TestCase):
    """`qn confirm` is the only thing allowed to put a name on a line."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qn-confirm-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = pathlib.Path(self.dir)
        rows = [{"offsets": {"from": 0, "to": 2000}, "text": "shall I start"},
                {"offsets": {"from": 9000, "to": 11_000}, "text": "yes go ahead"}]
        (self.path / "them.json").write_text(json.dumps({"transcription": rows}))
        (self.path / "speakers.json").write_text(json.dumps(
            [{"start_ms": 0, "end_ms": 3000, "speaker": "A"},
             {"start_ms": 8000, "end_ms": 12_000, "speaker": "B"}]))

    def confirm(self, text):
        (self.path / "confirmed.txt").write_text(text)

    def test_without_a_confirmation_the_letter_stays(self):
        self.assertEqual([row[2] for row in merge.read_segments(self.path)],
                         ["Them A", "Them B"])

    def test_a_confirmed_voice_becomes_the_person(self):
        self.confirm("A=Marco\n")
        self.assertEqual([row[2] for row in merge.read_segments(self.path)],
                         ["Marco", "Them B"])

    def test_each_voice_is_confirmed_on_its_own(self):
        self.confirm("A=Marco\nB=Lena\n")
        self.assertEqual([row[2] for row in merge.read_segments(self.path)],
                         ["Marco", "Lena"])

    def test_the_letter_is_read_case_insensitively(self):
        self.confirm("a=Marco\n")
        self.assertEqual(merge.read_confirmed(self.path), {"A": "Marco"})

    def test_a_blank_name_confirms_nothing(self):
        self.confirm("A=\n")
        self.assertEqual(merge.read_confirmed(self.path), {})

    def test_a_junk_line_is_skipped_without_losing_the_good_ones(self):
        self.confirm("not a mapping\nA=Marco\n")
        self.assertEqual(merge.read_confirmed(self.path), {"A": "Marco"})

    def test_a_multi_letter_key_is_refused(self):
        """Only the letters diarize.py hands out can be confirmed."""
        self.confirm("AB=Marco\n")
        self.assertEqual(merge.read_confirmed(self.path), {})

    def test_no_file_means_no_confirmations(self):
        self.assertEqual(merge.read_confirmed(self.path), {})

    def test_a_confirmed_name_survives_being_rebuilt(self):
        """`qn redo` re-runs merge.py, so the file beside the audio is what
        keeps your answer from being thrown away."""
        self.confirm("A=Marco\n")
        first = merge.format_turns(merge.build_turns(merge.read_segments(self.path)))
        second = merge.format_turns(merge.build_turns(merge.read_segments(self.path)))
        self.assertEqual(first, second)
        self.assertIn("Marco:", first)


if __name__ == "__main__":
    unittest.main()
