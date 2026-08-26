"""Tests for the capture health check.

These exercise parsing and judgement only. Both are pure, so no audio file and
no ffmpeg process is involved.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib"), os.path.join(ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import unittest

from health import Measurement, _parse, advise, judge

GOOD = """  Duration: 00:03:57.97, start: 0.044000, bitrate: 75 kb/s
[Parsed_volumedetect_0 @ 0x7cec1ce40] mean_volume: -32.8 dB
[Parsed_volumedetect_0 @ 0x7cec1ce40] max_volume: -3.0 dB
"""


def ok(seconds=240.0):
    return Measurement(seconds, -22.0, -3.0)


class ParseFfmpegOutput(unittest.TestCase):
    def test_reads_duration_and_levels(self):
        found = _parse(GOOD)
        self.assertAlmostEqual(found.seconds, 237.97, places=2)
        self.assertEqual(found.mean_db, -32.8)
        self.assertEqual(found.peak_db, -3.0)

    def test_missing_values_stay_none(self):
        found = _parse("ffmpeg said nothing useful")
        self.assertIsNone(found.seconds)
        self.assertIsNone(found.mean_db)
        self.assertFalse(found.present)

    def test_duration_over_an_hour(self):
        self.assertAlmostEqual(_parse("  Duration: 01:04:12.50,").seconds, 3852.5, places=1)


class Judge(unittest.TestCase):
    def test_healthy_recording_has_no_warnings(self):
        self.assertEqual(judge({"them": ok(), "me": ok()}), [])

    def test_quiet_but_real_mic_is_not_flagged_silent(self):
        # Calibration guard: a real recording measured -32.8 dB mean. If a
        # threshold ever drifts above that, every meeting warns and the whole
        # feature becomes noise people ignore.
        warnings = judge({"them": ok(), "me": Measurement(240.0, -32.8, -3.0)})
        self.assertEqual(warnings, [])

    def test_missing_track_is_reported(self):
        warnings = judge({"them": Measurement(None, None, None), "me": ok()})
        self.assertEqual(len(warnings), 1)
        self.assertIn("them track is missing", warnings[0])

    def test_silent_track_is_reported(self):
        warnings = judge({"them": ok(), "me": Measurement(240.0, -70.0, -60.0)})
        self.assertTrue(any("silent" in w for w in warnings))

    def test_very_quiet_track_warns_without_claiming_silence(self):
        warnings = judge({"them": ok(), "me": Measurement(240.0, -45.0, -20.0)})
        self.assertTrue(any("very quiet" in w for w in warnings))
        self.assertFalse(any("silent" in w for w in warnings))

    def test_clipping_is_reported(self):
        warnings = judge({"them": ok(), "me": Measurement(240.0, -12.0, 0.0)})
        self.assertTrue(any("clipping" in w for w in warnings))

    def test_too_short_is_reported(self):
        warnings = judge({"them": ok(), "me": Measurement(2.0, -22.0, -3.0)})
        self.assertTrue(any("only 2s" in w for w in warnings))

    def test_one_track_stopping_early_is_reported(self):
        warnings = judge({"them": ok(600.0), "me": ok(120.0)})
        self.assertTrue(any("stopped well before" in w for w in warnings))

    def test_matched_lengths_do_not_warn(self):
        self.assertEqual(judge({"them": ok(600.0), "me": ok(597.0)}), [])

    def test_both_tracks_missing_reports_both(self):
        empty = Measurement(None, None, None)
        self.assertEqual(len(judge({"them": empty, "me": empty})), 2)


def report(me=None, them=None):
    """A health report shaped the way check() returns one."""
    return {"tracks": {"me": me, "them": them}}


def track(seconds=8.0, mean_db=-28.0, peak_db=-6.0):
    return {"seconds": seconds, "mean_db": mean_db, "peak_db": peak_db}


MISSING = {"seconds": None, "mean_db": None, "peak_db": None}


class MicrophoneTestAdvice(unittest.TestCase):
    """What `qn doctor --mic` tells the user.

    A meeting whose `them` track is silent is broken. A microphone test whose
    `them` track is silent is working — nothing was playing. These assert the
    two are never confused, because saying "broken" to a working setup sends
    people to fix a permission that was already granted.
    """

    def verdicts(self, **kwargs):
        return [verdict for verdict, _ in advise(report(**kwargs))]

    def messages(self, **kwargs):
        return " ".join(message for _, message in advise(report(**kwargs)))

    def test_a_working_microphone_passes(self):
        self.assertEqual(self.verdicts(me=track(), them=track()), ["ok", "ok"])

    def test_a_silent_system_track_is_not_a_failure(self):
        # Nothing was playing. That is the normal case for a test.
        verdicts = self.verdicts(me=track(), them=track(mean_db=-70.0))
        self.assertEqual(verdicts, ["ok", "ok"])
        self.assertIn("nothing was playing", self.messages(me=track(), them=track(mean_db=-70.0)))

    def test_a_missing_microphone_names_the_permission(self):
        self.assertEqual(self.verdicts(me=MISSING, them=track())[0], "fail")
        self.assertIn("Microphone", self.messages(me=MISSING, them=track()))

    def test_a_missing_system_track_names_the_permission(self):
        self.assertEqual(self.verdicts(me=track(), them=MISSING)[1], "fail")
        self.assertIn("Screen Recording", self.messages(me=track(), them=MISSING))

    def test_a_silent_microphone_fails(self):
        self.assertEqual(self.verdicts(me=track(mean_db=-70.0), them=track())[0], "fail")
        self.assertIn("muted", self.messages(me=track(mean_db=-70.0), them=track()))

    def test_a_quiet_microphone_warns_but_does_not_fail(self):
        self.assertEqual(self.verdicts(me=track(mean_db=-45.0), them=track())[0], "warn")

    def test_a_clipping_microphone_warns(self):
        self.assertEqual(self.verdicts(me=track(peak_db=-0.1), them=track())[0], "warn")

    def test_an_unreadable_microphone_track_fails(self):
        broken = {"seconds": 8.0, "mean_db": None, "peak_db": None}
        self.assertEqual(self.verdicts(me=broken, them=track())[0], "fail")

    def test_a_missing_track_key_is_treated_as_absent(self):
        # check() always supplies both keys, but advise() must not crash if a
        # caller hands it less.
        self.assertEqual(advise({"tracks": {}}), advise(report(me=None, them=None)))

    def test_every_verdict_is_one_of_three_words(self):
        for verdict, _ in advise(report(me=track(), them=track())):
            self.assertIn(verdict, ("ok", "warn", "fail"))


if __name__ == "__main__":
    unittest.main()
