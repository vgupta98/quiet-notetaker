"""Tests for stitching the two tracks back into one conversation."""

import unittest

from merge import build_turns, format_turns


class BuildTurns(unittest.TestCase):
    def test_orders_by_time_across_tracks(self):
        turns = build_turns([(5000, "Me", "second"), (1000, "Them", "first")])
        self.assertEqual([t[1] for t in turns], ["Them", "Me"])

    def test_joins_a_continuous_speaker(self):
        turns = build_turns([(0, "Them", "one"), (2000, "Them", "two")])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0][2], "one two")

    def test_breaks_a_turn_after_a_long_pause(self):
        # The real failure this fixes: a single Them turn ran 96 seconds and
        # rendered as one unreadable block.
        turns = build_turns([(0, "Them", "one"), (30_000, "Them", "two")])
        self.assertEqual(len(turns), 2)

    def test_a_short_gap_does_not_break_a_turn(self):
        turns = build_turns([(0, "Them", "one"), (7_000, "Them", "two")])
        self.assertEqual(len(turns), 1)

    def test_speaker_change_always_breaks(self):
        turns = build_turns([(0, "Them", "a"), (100, "Me", "b"), (200, "Them", "c")])
        self.assertEqual(len(turns), 3)

    def test_pause_is_measured_per_speaker(self):
        # Them keeps talking either side of a short Me interjection. That is one
        # continuous Them turn, not two.
        turns = build_turns([(0, "Them", "a"), (1000, "Me", "yes"), (2000, "Them", "b")])
        self.assertEqual([t[1] for t in turns], ["Them", "Me", "Them"])

    def test_empty_input(self):
        self.assertEqual(build_turns([]), [])


class FormatTurns(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(format_turns([(0, "Me", "hi")]), "[00:00] Me: hi")

    def test_minutes_and_seconds(self):
        self.assertEqual(format_turns([(97_440, "Them", "x")]), "[01:37] Them: x")

    def test_beyond_an_hour_keeps_counting_minutes(self):
        self.assertEqual(format_turns([(3_930_000, "Me", "x")]), "[65:30] Me: x")


if __name__ == "__main__":
    unittest.main()
