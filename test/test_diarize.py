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
    """What you told `qn confirm` outranks every other source of a name."""

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


class RosterMatches(unittest.TestCase):
    """`matched.txt` names a line too, but never over your own answer."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qn-matched-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = pathlib.Path(self.dir)
        rows = [{"offsets": {"from": 0, "to": 2000}, "text": "shall I start"},
                {"offsets": {"from": 9000, "to": 11_000}, "text": "yes go ahead"}]
        (self.path / "them.json").write_text(json.dumps({"transcription": rows}))
        (self.path / "speakers.json").write_text(json.dumps(
            [{"start_ms": 0, "end_ms": 3000, "speaker": "A"},
             {"start_ms": 8000, "end_ms": 12_000, "speaker": "B"}]))

    def labels(self):
        return [row[2] for row in merge.read_segments(self.path)]

    def test_a_matched_voice_becomes_the_person(self):
        (self.path / "matched.txt").write_text("A=Aisha\n")
        self.assertEqual(self.labels(), ["Aisha", "Them B"])

    def test_your_confirmation_beats_the_roster(self):
        (self.path / "matched.txt").write_text("A=Aisha\n")
        (self.path / "confirmed.txt").write_text("A=Tom\n")
        self.assertEqual(self.labels(), ["Tom", "Them B"])

    def test_the_roster_still_names_the_voices_you_did_not_confirm(self):
        (self.path / "matched.txt").write_text("A=Aisha\nB=Marco\n")
        (self.path / "confirmed.txt").write_text("A=Tom\n")
        self.assertEqual(self.labels(), ["Tom", "Marco"])

    def test_no_matched_file_reads_exactly_as_before(self):
        self.assertEqual(self.labels(), ["Them A", "Them B"])

    def test_a_junk_line_is_skipped_without_losing_the_good_ones(self):
        (self.path / "matched.txt").write_text("nonsense\nB=Marco\n")
        self.assertEqual(self.labels(), ["Them A", "Marco"])


def said(speaker, start_ms, seconds):
    return {"start_ms": start_ms, "end_ms": start_ms + int(seconds * 1000),
            "speaker": speaker}


class ChoosingAudioForAVoiceprint(unittest.TestCase):
    """`print_ranges` picks which audio represents a voice. No model needed."""

    def test_the_longest_stretch_is_taken_first(self):
        ranges = diarize.print_ranges([said("A", 0, 3), said("A", 60_000, 25)])
        self.assertEqual(ranges["A"][0], (0, 3_000))       # sorted by time...
        self.assertEqual(len(ranges["A"]), 2)
        # ...but the 25-second stretch got its full length, the 3-second one
        # only what the budget had left.
        self.assertEqual(ranges["A"][1], (60_000, 85_000))

    def test_the_budget_is_the_stated_one(self):
        spans = diarize.print_ranges([said("A", 0, 100)])["A"]
        total = sum(end - start for start, end in spans)
        self.assertEqual(total, int(diarize.PRINT_SECONDS * 1000))

    def test_a_segment_is_cut_short_when_the_budget_runs_out(self):
        spans = diarize.print_ranges([said("A", 0, 20), said("A", 30_000, 15)])["A"]
        self.assertEqual(spans, [(0, 20_000), (30_000, 40_000)])

    def test_a_voice_shorter_than_the_budget_gives_all_it_has(self):
        spans = diarize.print_ranges([said("A", 0, 5)])["A"]
        self.assertEqual(spans, [(0, 5_000)])

    def test_ranges_come_back_in_time_order(self):
        spans = diarize.print_ranges([said("A", 90_000, 4), said("A", 10_000, 8)])["A"]
        self.assertEqual(spans, sorted(spans))

    def test_each_voice_gets_its_own_budget(self):
        ranges = diarize.print_ranges([said("A", 0, 100), said("B", 200_000, 100)])
        for letter in ("A", "B"):
            total = sum(end - start for start, end in ranges[letter])
            self.assertEqual(total, int(diarize.PRINT_SECONDS * 1000))

    def test_nothing_labelled_means_nothing_to_print(self):
        self.assertEqual(diarize.print_ranges([]), {})


class WorthRemembering(unittest.TestCase):
    """A voice must talk long enough before the tool offers to remember it."""

    def test_talking_time_adds_up_per_voice(self):
        rows = [said("A", 0, 30), said("A", 60_000, 30), said("B", 0, 10)]
        self.assertEqual(diarize.talking(rows), {"A": 60.0, "B": 10.0})

    def test_the_floor_is_the_stated_one(self):
        just_under = diarize.MIN_PRINT_SECONDS - 0.1
        self.assertEqual(diarize.worth_remembering([said("A", 0, just_under)]), set())
        self.assertEqual(diarize.worth_remembering([said("A", 0, diarize.MIN_PRINT_SECONDS)]),
                         {"A"})

    def test_the_floor_is_higher_than_the_one_for_a_letter(self):
        # A voice can earn a letter in the transcript and still be too thin to
        # recognise next month. Measured: a 26-second sample lowered every
        # later score by about 0.03.
        self.assertGreater(diarize.MIN_PRINT_SECONDS, diarize.MIN_SPEAKER_SECONDS)

    def test_a_short_voice_keeps_its_letter_and_its_audio(self):
        rows = [said("A", 0, 26)]
        self.assertEqual(diarize.worth_remembering(rows), set())
        self.assertIn("A", diarize.print_ranges(rows))   # qn play still works


class PlayingOneVoice(unittest.TestCase):
    """`qn play <id> <letter>` plays what the voiceprint was built from."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qn-playvoice-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def speakers(self, rows):
        with open(os.path.join(self.dir, diarize.SPEAKERS_FILE), "w", encoding="utf-8") as h:
            json.dump(rows, h)

    def test_spans_come_back_in_seconds(self):
        self.speakers([said("A", 90_000, 25)])
        self.assertEqual(diarize.play_spans(self.dir, "A"), [(90.0, 115.0)])

    def test_they_are_the_spans_the_voiceprint_used(self):
        # The promise of this command: you hear what the roster heard.
        rows = [said("A", 0, 20), said("A", 30_000, 15), said("B", 60_000, 40)]
        self.speakers(rows)
        ranges = diarize.print_ranges(rows)["A"]
        self.assertEqual(diarize.play_spans(self.dir, "A"),
                         [(start / 1000, end / 1000) for start, end in ranges])

    def test_a_lower_case_letter_finds_the_voice(self):
        self.speakers([said("A", 0, 25)])
        self.assertEqual(diarize.play_spans(self.dir, "a"), [(0.0, 25.0)])

    def test_an_unknown_letter_has_nothing_to_play(self):
        self.speakers([said("A", 0, 25)])
        self.assertEqual(diarize.play_spans(self.dir, "Z"), [])

    def test_a_meeting_without_groups_has_nothing_to_play(self):
        self.assertEqual(diarize.play_spans(self.dir, "A"), [])


class ReadingVoiceprints(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, text):
        with open(os.path.join(self.dir, diarize.PRINTS_FILE), "w", encoding="utf-8") as h:
            h.write(text)

    def test_no_file_means_no_prints(self):
        self.assertEqual(diarize.read_prints(self.dir), {})

    def test_a_damaged_file_is_ignored(self):
        self.write("{ not json")
        self.assertEqual(diarize.read_prints(self.dir), {})

    def test_a_list_where_an_object_belongs_is_ignored(self):
        self.write("[1, 2]")
        self.assertEqual(diarize.read_prints(self.dir), {})

    def test_a_good_file_reads_back(self):
        self.write(json.dumps({"A": [0.5, -0.25], "B": [1.0, 0.0]}))
        self.assertEqual(diarize.read_prints(self.dir), {"A": [0.5, -0.25], "B": [1.0, 0.0]})

    def test_an_empty_vector_is_dropped(self):
        self.write(json.dumps({"A": [], "B": [1.0]}))
        self.assertEqual(diarize.read_prints(self.dir), {"B": [1.0]})

    def test_a_vector_of_strings_is_dropped(self):
        self.write(json.dumps({"A": ["loud"], "B": [1.0]}))
        self.assertEqual(diarize.read_prints(self.dir), {"B": [1.0]})


class Vectors(unittest.TestCase):
    def test_a_unit_vector_has_length_one(self):
        self.assertAlmostEqual(
            sum(v * v for v in diarize.unit([3.0, 4.0])), 1.0)

    def test_a_vector_of_zeroes_has_no_direction(self):
        self.assertEqual(diarize.unit([0.0, 0.0]), [])

    def test_the_same_direction_scores_one(self):
        self.assertAlmostEqual(
            diarize.similarity(diarize.unit([2.0, 0.0]), diarize.unit([9.0, 0.0])), 1.0)

    def test_a_right_angle_scores_nothing(self):
        self.assertAlmostEqual(
            diarize.similarity(diarize.unit([1.0, 0.0]), diarize.unit([0.0, 1.0])), 0.0)

    def test_a_missing_vector_scores_nothing(self):
        self.assertEqual(diarize.similarity([], diarize.unit([1.0, 0.0])), 0.0)

    def test_the_centre_sits_between_two_voices(self):
        middle = diarize.centre([diarize.unit([1.0, 0.0]), diarize.unit([0.0, 1.0])])
        self.assertAlmostEqual(middle[0], middle[1])


# Three directions far enough apart that nothing merges them, plus near-copies.
EAST, NORTH, UP = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
EAST_ISH, NORTH_ISH = [0.95, 0.05, 0.0], [0.05, 0.95, 0.0]


class Clustering(unittest.TestCase):
    def test_two_voices_stay_two_groups(self):
        vectors = [EAST, EAST_ISH, NORTH, NORTH_ISH]
        groups = diarize.cluster(vectors, [15.0, 15.0, 15.0, 15.0])
        self.assertEqual([sorted(g) for g in groups], [[0, 1], [2, 3]])

    def test_the_busiest_group_comes_first(self):
        vectors = [NORTH, NORTH_ISH, EAST, EAST_ISH]
        groups = diarize.cluster(vectors, [11.0, 11.0, 30.0, 30.0])
        self.assertEqual(sorted(groups[0]), [2, 3])

    def test_a_short_span_never_decides_who_exists(self):
        # The east pair is long. The north pair is too short to have a say.
        groups = diarize.cluster([EAST, EAST_ISH, NORTH, NORTH_ISH],
                                 [15.0, 15.0, 3.9, 3.9])
        self.assertEqual([sorted(g) for g in groups], [[0, 1]])

    def test_a_span_of_exactly_the_floor_still_counts(self):
        # Five spans of exactly 4s reach the 20s a group needs to survive.
        vectors = [EAST, EAST_ISH] * 2 + [EAST]
        groups = diarize.cluster(vectors, [diarize.RELIABLE_SECONDS] * 5)
        self.assertEqual([sorted(g) for g in groups], [[0, 1, 2, 3, 4]])

    def test_a_quiet_group_is_dropped(self):
        # Both pairs are reliable spans, but the north pair barely talks.
        groups = diarize.cluster([EAST, EAST_ISH, NORTH, NORTH_ISH],
                                 [15.0, 15.0, 5.0, 5.0])
        self.assertEqual([sorted(g) for g in groups], [[0, 1]])

    def test_a_span_with_no_vector_is_ignored(self):
        groups = diarize.cluster([EAST, EAST_ISH, []], [15.0, 15.0, 60.0])
        self.assertEqual([sorted(g) for g in groups], [[0, 1]])

    def test_nothing_reliable_means_no_groups(self):
        self.assertEqual(diarize.cluster([EAST, NORTH], [1.0, 1.0]), [])


class Assigning(unittest.TestCase):
    def setUp(self):
        self.vectors = [EAST, EAST_ISH, NORTH, NORTH_ISH]
        self.groups = diarize.cluster(self.vectors, [15.0, 15.0, 15.0, 15.0])

    def test_a_span_joins_the_voice_it_resembles(self):
        self.assertEqual(diarize.assign(self.vectors, self.groups), [0, 0, 1, 1])

    def test_a_short_span_is_placed_too(self):
        # This is the point of the step: scraps get a seat, just never a vote.
        vectors = self.vectors + [[0.9, 0.1, 0.0]]
        self.assertEqual(diarize.assign(vectors, self.groups)[-1], 0)

    def test_a_span_that_resembles_nobody_is_left_alone(self):
        # A third direction, at a right angle to both voices in the room.
        self.assertIsNone(diarize.assign(self.vectors + [UP], self.groups)[-1])

    def test_a_span_with_no_vector_is_left_alone(self):
        self.assertIsNone(diarize.assign(self.vectors + [[]], self.groups)[-1])

    def test_no_groups_means_nobody_is_placed(self):
        self.assertEqual(diarize.assign(self.vectors, []), [None] * 4)


if __name__ == "__main__":
    unittest.main()
