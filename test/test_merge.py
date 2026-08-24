"""Tests for stitching the two tracks back into one conversation.

Segments carry (start_ms, end_ms, speaker, text). The end matters: measuring a
pause from the previous segment's START made every long segment look like a
pause, and whisper emits segments up to 30 seconds long.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib"), os.path.join(ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import unittest

from merge import build_turns, format_turns


class BuildTurns(unittest.TestCase):
    def test_orders_by_time_across_tracks(self):
        turns = build_turns([(5000, 6000, "Me", "second"), (1000, 2000, "Them", "first")])
        self.assertEqual([t[1] for t in turns], ["Them", "Me"])

    def test_joins_a_continuous_speaker(self):
        turns = build_turns([(0, 1000, "Them", "one"), (2000, 3000, "Them", "two")])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0][2], "one two")

    def test_a_long_segment_is_not_a_pause(self):
        # The bug this guards: a 9s segment followed immediately by the next
        # one used to break the turn, because the gap was measured from the
        # first segment's start rather than its end.
        turns = build_turns([(0, 9000, "Them", "one"), (9000, 12000, "Them", "two")])
        self.assertEqual(len(turns), 1)

    def test_a_thirty_second_segment_is_not_a_pause(self):
        turns = build_turns([(0, 30000, "Them", "one"), (30100, 31000, "Them", "two")])
        self.assertEqual(len(turns), 1)

    def test_breaks_a_turn_after_a_real_pause(self):
        turns = build_turns([(0, 1000, "Them", "one"), (30_000, 31_000, "Them", "two")])
        self.assertEqual(len(turns), 2)

    def test_a_short_gap_does_not_break_a_turn(self):
        turns = build_turns([(0, 1000, "Them", "one"), (7_000, 8_000, "Them", "two")])
        self.assertEqual(len(turns), 1)

    def test_speaker_change_always_breaks(self):
        turns = build_turns(
            [(0, 100, "Them", "a"), (100, 200, "Me", "b"), (200, 300, "Them", "c")]
        )
        self.assertEqual(len(turns), 3)

    def test_pause_is_measured_per_speaker(self):
        # A short Me interjection between two Them segments makes three turns,
        # because the speaker changed twice. The point of the per-speaker clock
        # is that Them's pause is measured against Them, not against Me.
        turns = build_turns(
            [(0, 500, "Them", "a"), (1000, 1500, "Me", "yes"), (2000, 2500, "Them", "b")]
        )
        self.assertEqual([t[1] for t in turns], ["Them", "Me", "Them"])

    def test_an_interjection_does_not_reset_the_other_speaker(self):
        # Them talks 0-1s and 40-41s, so Them paused for 39 seconds and must
        # break, even though Me spoke in between.
        turns = build_turns(
            [(0, 1000, "Them", "a"), (2000, 2500, "Me", "mm"), (40_000, 41_000, "Them", "b")]
        )
        self.assertEqual([t[1] for t in turns], ["Them", "Me", "Them"])

    def test_out_of_order_input_is_sorted(self):
        # Kept inside one turn deliberately: an 8.8s gap would break the turn
        # and hide whether sorting happened at all.
        turns = build_turns([(3000, 3500, "Me", "later"), (100, 200, "Me", "earlier")])
        self.assertEqual(turns[0][2], "earlier later")

    def test_empty_input(self):
        self.assertEqual(build_turns([]), [])


class FormatTurns(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(format_turns([(0, "Me", "hi")]), "[00:00] Me: hi")

    def test_minutes_and_seconds(self):
        self.assertEqual(format_turns([(97_440, "Them", "x")]), "[01:37] Them: x")

    def test_beyond_an_hour_keeps_counting_minutes(self):
        self.assertEqual(format_turns([(3_930_000, "Me", "x")]), "[65:30] Me: x")


class ReadSegments(unittest.TestCase):
    """read_segments must keep the end offset, not discard it."""

    def test_reads_start_and_end_from_whisper_json(self):
        import json
        import pathlib
        import tempfile

        from merge import read_segments

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)
            (path / "them.json").write_text(
                json.dumps(
                    {
                        "transcription": [
                            {"offsets": {"from": 0, "to": 9000}, "text": " hello"},
                            {"offsets": {"from": 9000, "to": 12000}, "text": " again"},
                        ]
                    }
                )
            )
            segments = read_segments(path)

        self.assertEqual(segments[0], (0, 9000, "Them", "hello"))
        # And end-to-end: those two must stay one turn.
        self.assertEqual(len(build_turns(segments)), 1)


if __name__ == "__main__":
    unittest.main()
