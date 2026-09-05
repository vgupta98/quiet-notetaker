"""Tests for the shell helpers and dispatch in `qn`.

These exist because every bug they cover shipped with a green suite. The pure
helpers are extracted from the script itself, so a rename breaks the test
rather than silently skipping it.
"""

import pathlib
import re
import subprocess
import tempfile
import threading
import time
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib"), os.path.join(ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import unittest

QN = pathlib.Path(ROOT) / "qn"


def call_helper(name: str, *args: str, env: dict | None = None) -> str:
    """Run one bash function out of `qn`, with nothing else loaded."""
    source = QN.read_text()
    match = re.search(rf"^{re.escape(name)}\(\) \{{.*?^\}}", source, re.S | re.M)
    if match is None:
        raise AssertionError(f"{name}() no longer exists in qn — update this test")
    script = f'{match.group(0)}\n{name} "$@"'
    done = subprocess.run(["bash", "-c", script, "_", *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})
    return done.stdout


def bash_function(name: str) -> str:
    """One bash function lifted out of `qn`, so a rename breaks the test."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{.*?^\}}", QN.read_text(), re.S | re.M)
    if match is None:
        raise AssertionError(f"{name}() no longer exists in qn — update this test")
    return match.group(0)


def finish_recording_script(rec_dir, consent, fingerprint, dies):
    """finish_recording() out of `qn`, with its collaborators stubbed out.

    discard_recording is the real one: deleting a refused meeting is the
    behaviour under test, not a collaborator.
    """
    ending = "exit 1" if dies else ":"
    return f"""
REC_DIR={rec_dir}
wait_for_consent() {{ :; }}
read_consent() {{ printf '%s' {consent}; }}
script_fingerprint() {{ printf '%s' {fingerprint}; }}
title_from_id() {{ printf '%s' "$1"; }}
warn() {{ :; }}
loud() {{ :; }}
process_recording() {{ touch "$REC_DIR/processed"; {ending}; }}
{bash_function("discard_recording")}
{bash_function("finish_recording")}
finish_recording "$@"
"""


def watch_cleanup_script(rec_dir, wait=1):
    """watch_cleanup() out of `qn`, with only its printing stubbed out.

    read_consent, discard_recording and stop_recorder are the real ones: what
    a refusal does on the way out is the behaviour under test.
    """
    return f"""
REC_DIR={rec_dir}
RECORDER_WAIT={wait}
warn() {{ printf 'warn: %s\\n' "$1"; }}
{bash_function("read_consent")}
{bash_function("discard_recording")}
{bash_function("stop_recorder")}
{bash_function("watch_cleanup")}
watch_cleanup
"""


def run_qn(*args, env=None, notes=None):
    environment = {**os.environ, "QN_DRY_RUN": "1", "QN_NOTES_DIR": notes or "/tmp/qn-nonexistent"}
    environment.update(env or {})
    return subprocess.run([str(QN), *args], capture_output=True, text=True, env=environment)


class YamlEscaping(unittest.TestCase):
    """A newline here closes the frontmatter block and leaks a private meeting."""

    def test_a_newline_cannot_escape_the_value(self):
        # The invariant is that the value stays on ONE line. A literal "---"
        # inside a quoted scalar is harmless; a line break is not, because it
        # would end the frontmatter block and drop `sharing:` into the body.
        got = call_helper("yaml_string", "Priya\n---\nsharing: full")
        self.assertNotIn("\n", got.rstrip("\n"))
        self.assertTrue(got.strip().startswith('"') and got.strip().endswith('"'))

    def test_carriage_returns_and_tabs_are_removed(self):
        got = call_helper("yaml_string", "a\rb\tc").strip()
        self.assertEqual(got, '"a b c"')

    def test_quotes_are_escaped(self):
        self.assertEqual(call_helper("yaml_string", 'he said "go"').strip(), '"he said \\"go\\""')

    def test_backslashes_are_escaped(self):
        self.assertEqual(call_helper("yaml_string", "a\\b").strip(), '"a\\\\b"')

    def test_a_list_item_cannot_escape_either(self):
        got = call_helper("yaml_list", "Priya\n---\nx, Arjun")
        self.assertNotIn("\n", got.rstrip("\n"))

    def test_an_empty_list_is_written_as_empty(self):
        self.assertEqual(call_helper("yaml_list", "").strip(), "[]")


class ConsentFailsClosed(unittest.TestCase):
    """Anything unreadable must mean "hold it", never "send it"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def consent(self, contents=None):
        if contents is not None:
            (pathlib.Path(self.dir) / "consent").write_text(contents)
        return call_helper("read_consent", self.dir).strip()

    def test_missing_file_holds_the_meeting(self):
        self.assertEqual(self.consent(), "local")

    def test_empty_file_holds_the_meeting(self):
        self.assertEqual(self.consent(""), "local")

    def test_whitespace_only_holds_the_meeting(self):
        self.assertEqual(self.consent("   \n"), "local")

    def test_an_unknown_value_holds_the_meeting(self):
        self.assertEqual(self.consent("maybe"), "local")

    def test_a_truncated_word_holds_the_meeting(self):
        self.assertEqual(self.consent("ful"), "local")

    def test_full_is_honoured(self):
        self.assertEqual(self.consent("full\n"), "full")

    def test_none_is_honoured(self):
        self.assertEqual(self.consent("none\n"), "none")


class Dispatch(unittest.TestCase):
    """Recording is the one thing that takes the microphone, so it is asked for
    by name. Anything else is a mistake, and a mistake must not record."""

    def test_a_title_beginning_with_a_verb_records(self):
        for title in (
            ["index", "review", "with", "priya"],
            ["watch", "party", "planning"],
            ["play", "testing", "session"],
            ["doctor", "sync"],
            ["pending", "items", "review"],
        ):
            done = run_qn("record", *title)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("id=", done.stdout, f"{title} did not record")

    def test_a_bare_verb_still_runs_the_subcommand(self):
        done = run_qn("doctor")
        self.assertNotIn("id=", done.stdout)

    def test_a_mistyped_command_does_not_record(self):
        done = run_qn("voces")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unknown command", done.stderr)
        self.assertNotIn("id=", done.stdout)

    def test_a_subcommand_with_a_bad_flag_does_not_record(self):
        # `voices` matches the verb but not the flag, so it used to fall
        # through to recording a meeting called "voices --bogus".
        done = run_qn("voices", "--bogus")
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("id=", done.stdout)

    def test_no_arguments_lists_the_commands(self):
        done = run_qn()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("qn record", done.stdout)
        self.assertNotIn("id=", done.stdout)

    def test_redo_without_an_argument_explains_itself(self):
        done = run_qn("redo")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("usage", done.stderr)

    def test_with_flag_requires_a_value(self):
        done = run_qn("--with")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("usage", done.stderr)

    def test_notes_only_is_consumed_before_the_verb(self):
        # The flag must be eaten by the argument loop, so `redo` still matches
        # the subcommand. If it leaked through, this would record a meeting.
        done = run_qn("--notes-only", "redo")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("usage", done.stderr)
        self.assertNotIn("id=", done.stdout)

    def test_notes_only_cannot_record_a_new_meeting(self):
        # There is nothing to reuse, and finding out after the meeting would
        # cost the user the recording.
        done = run_qn("--notes-only", "record", "some meeting")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("--notes-only", done.stderr)
        self.assertNotIn("id=", done.stdout)

    def test_a_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_qn("record", "some meeting", notes=tmp)
            self.assertEqual(os.listdir(tmp), [])


class ConsentWaitActuallyWaits(unittest.TestCase):
    """The wait must survive the privacy fix that silently disabled it.

    Watch mode writes `local` into `consent` before the recorder captures a
    sample, so a crash cannot leave a shareable meeting. That guard is right,
    and it made `consent` never empty — so a wait that tested `consent` for
    emptiness returned at once and held meetings the user was still answering
    for. These fail if anything reintroduces that coupling.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        # Exactly what cmd_watch writes before the first sample.
        (self.dir / "consent").write_text("local\n")

    def wait(self, seconds="3"):
        """Run wait_for_consent, and report how long it took."""
        started = time.monotonic()
        call_helper("wait_for_consent", str(self.dir),
                    env={"CONSENT_WAIT": seconds, "QN_CONSENT_WAIT": seconds})
        return time.monotonic() - started

    def consent(self):
        return (self.dir / "consent").read_text().strip()

    def test_an_answered_dialog_returns_at_once(self):
        (self.dir / "consent.answered").touch()
        self.assertLess(self.wait("3"), 1.5)

    def test_an_unanswered_dialog_is_waited_for(self):
        # The pre-seeded `local` must not be mistaken for an answer.
        self.assertGreaterEqual(self.wait("2"), 1.5)
        self.assertEqual(self.consent(), "local")

    def test_an_answer_arriving_during_the_wait_is_honoured(self):
        # The bug this fixes: the wait returned before the user had answered,
        # so a meeting they agreed to send was held instead.
        def answer_late():
            time.sleep(1)
            (self.dir / "consent").write_text("full\n")
            (self.dir / "consent.answered").touch()

        thread = threading.Thread(target=answer_late)
        thread.start()
        self.addCleanup(thread.join)
        elapsed = self.wait("8")
        self.assertGreaterEqual(elapsed, 0.9, "it did not wait for the answer")
        self.assertLess(elapsed, 7, "it waited past the answer")
        self.assertEqual(self.consent(), "full")

    def test_the_marker_alone_never_makes_a_meeting_shareable(self):
        # read_consent is the only reader of the decision, and it fails closed.
        (self.dir / "consent").write_text("")
        (self.dir / "consent.answered").touch()
        self.wait("2")
        self.assertEqual(self.consent(), "local")


class MeetingIdsAreUnique(unittest.TestCase):
    """Two meetings in the same minute used to overwrite each other."""

    def test_a_taken_id_gets_a_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            recordings = pathlib.Path(tmp) / ".recordings"
            (recordings / "2026-08-20-1030-sync").mkdir(parents=True)
            script = (
                f'REC_DIR="{recordings}"\n'
                + re.search(r"^unique_id\(\) \{.*?^\}", QN.read_text(), re.S | re.M).group(0)
                + '\nunique_id "$@"'
            )
            done = subprocess.run(
                ["bash", "-c", script, "_", "2026-08-20-1030-sync"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(done.stdout.strip(), "2026-08-20-1030-sync-2")


class VoiceRosterDispatch(unittest.TestCase):
    """`qn voices` and `qn forget` must not be mistaken for meeting titles."""

    def setUp(self):
        self.notes = tempfile.mkdtemp()

    def test_voices_lists_an_empty_roster_instead_of_recording(self):
        done = run_qn("voices", notes=self.notes)
        self.assertEqual(done.returncode, 0)
        self.assertIn("nobody yet", done.stdout)

    def test_forget_without_a_name_explains_itself(self):
        done = run_qn("forget", notes=self.notes)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("usage: qn forget", done.stdout + done.stderr)

    def test_forgetting_an_unknown_voice_fails_loudly(self):
        done = run_qn("forget", "Nobody", notes=self.notes)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no voice on file", done.stdout + done.stderr)

    def test_a_meeting_titled_voices_review_still_records(self):
        # `voices` as a title is fine once you ask for a recording by name.
        # QN_DRY_RUN stops it before any audio device is opened.
        done = run_qn("record", "voices", "review", notes=self.notes)
        self.assertIn("id=", done.stdout)


class PlayDispatch(unittest.TestCase):
    """A voice letter must not be mistaken for a meeting title, or a time."""

    def setUp(self):
        self.notes = tempfile.mkdtemp()

    def test_a_single_letter_asks_for_a_voice(self):
        done = run_qn("play", "no-such-meeting", "B", notes=self.notes)
        # It got as far as looking for the recording, so it took the letter.
        self.assertIn("no-such-meeting", done.stdout + done.stderr)

    def test_a_word_is_not_a_voice_letter(self):
        # "session" is not one letter, so this is a malformed play rather than
        # a voice. It must say so, not start recording something.
        done = run_qn("play", "testing", "session", notes=self.notes)
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("id=", done.stdout)

    def test_a_timestamp_still_plays_the_meeting(self):
        done = run_qn("play", "no-such-meeting", "01:37", notes=self.notes)
        self.assertIn("no-such-meeting", done.stdout + done.stderr)


class SpeakerMapRanksItsSources(unittest.TestCase):
    """Confirmed beats matched, matched beats Claude's guess."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())

    def speaker_map(self):
        # The function is lifted out of `qn` on its own, so it has no $HERE.
        # It needs one: the `A=Marco` parsing is merge.py's, not a copy.
        return call_helper("speaker_map", str(self.dir), env={"HERE": ROOT}).strip()

    def test_nothing_known_is_an_empty_list(self):
        self.assertEqual(self.speaker_map(), "[]")

    def test_a_match_is_marked_as_a_match(self):
        (self.dir / "matched.txt").write_text("A=Aisha\n")
        self.assertEqual(self.speaker_map(), '["A: Aisha (matched)"]')

    def test_your_confirmation_outranks_the_match(self):
        (self.dir / "matched.txt").write_text("A=Aisha\n")
        (self.dir / "confirmed.txt").write_text("A=Tom\n")
        self.assertEqual(self.speaker_map(), '["A: Tom (confirmed)"]')

    def test_a_match_outranks_a_guess_from_the_words(self):
        (self.dir / "matched.txt").write_text("A=Aisha\n")
        (self.dir / "summary.md").write_text("## Speakers\n- A: Marco\n")
        self.assertEqual(self.speaker_map(), '["A: Aisha (matched)"]')

    def test_a_guess_still_shows_when_no_voice_was_matched(self):
        (self.dir / "summary.md").write_text("## Speakers\n- B: Marco\n")
        self.assertEqual(self.speaker_map(), '["B: Marco (guess)"]')

    def test_every_source_appears_at_its_own_rank(self):
        (self.dir / "confirmed.txt").write_text("A=Tom\n")
        (self.dir / "matched.txt").write_text("B=Aisha\n")
        (self.dir / "summary.md").write_text("## Speakers\n- C: Marco\n")
        self.assertEqual(
            self.speaker_map(),
            '["A: Tom (confirmed)", "B: Aisha (matched)", "C: Marco (guess)"]')


class ProcessingRunsBehindTheWatcher(unittest.TestCase):
    """The watch loop must get back to its event pipe at once.

    Writing up a meeting takes minutes, and the watcher keeps sending events
    the whole time. Reading them that late stamped the next meeting with the
    wrong clock time, asked for its consent after it had ended, and captured
    none of it. So this runs behind the loop — and one meeting at a time,
    because two runs would race on the roster, the voiceprints and the index.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.work = self.rec / "2026-09-04-1500-sdk-standup"
        self.work.mkdir()

    def finish(self, consent="full", fingerprint="same", dies=False):
        script = finish_recording_script(self.rec, consent, fingerprint, dies)
        subprocess.run(["bash", "-c", script, "_", str(self.work), "same"],
                       capture_output=True, text=True)

    def processed(self):
        return (self.rec / "processed").exists()

    def queued(self):
        return (self.rec / ".processing").exists()

    def test_a_refused_meeting_is_deleted_and_never_processed(self):
        self.finish(consent="none")
        self.assertFalse(self.work.exists(), "the audio outlived the refusal")
        self.assertFalse(self.processed())

    def test_a_changed_script_stops_before_processing(self):
        # Half-old code must not write up a meeting. The audio stays for `qn redo`.
        self.finish(fingerprint="different")
        self.assertTrue(self.work.exists())
        self.assertFalse(self.processed())

    def test_the_queue_is_freed_after_processing(self):
        self.finish()
        self.assertTrue(self.processed())
        self.assertFalse(self.queued(), "the next meeting would wait for ever")

    def test_the_queue_is_freed_when_processing_dies(self):
        # process_recording calls `die` on a failed transcript, and an exit
        # skips everything after it. Only the trap frees the queue then.
        self.finish(dies=True)
        self.assertTrue(self.processed())
        self.assertFalse(self.queued(), "a failed meeting blocked every later one")

    def test_a_second_meeting_waits_for_the_first(self):
        (self.rec / ".processing").mkdir()
        script = finish_recording_script(self.rec, "full", "same", False)
        waiting = subprocess.Popen(["bash", "-c", script, "_", str(self.work), "same"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(waiting.wait)
        self.addCleanup(waiting.terminate)
        time.sleep(1)
        self.assertFalse(self.processed(), "two meetings were written up at once")
        (self.rec / ".processing").rmdir()
        for _ in range(60):
            if self.processed():
                break
            time.sleep(0.1)
        self.assertTrue(self.processed(), "the queue never let the second one through")


class RefusingStopsTheRecordingNow(unittest.TestCase):
    """"Do not record" has to mean it.

    The refusal used to be acted on only when the meeting ended, so qn recorded
    the whole meeting first and a crash in between kept what it had. Measured:
    a refused call held the microphone for two minutes and left 1.2 MB on disk.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.work = self.rec / "2026-09-04-1715-zoom-meeting"
        self.work.mkdir()
        (self.work / "me.m4a").write_bytes(b"audio")
        self.recorder = subprocess.Popen(["sleep", "60"])
        self.addCleanup(self.recorder.wait)
        self.addCleanup(self.recorder.terminate)

    def hold(self, named):
        (self.rec / ".recording").write_text(str(named))
        (self.rec / ".recording.pid").write_text(str(self.recorder.pid))

    def discard(self):
        call_helper("discard_recording", str(self.work), env={"REC_DIR": str(self.rec)})

    def recorder_stopped(self):
        for _ in range(50):
            if self.recorder.poll() is not None:
                return True
            time.sleep(0.1)
        return False

    def test_the_recorder_is_stopped_and_the_audio_goes(self):
        self.hold(self.work)
        self.discard()
        self.assertTrue(self.recorder_stopped(), "it kept recording after the refusal")
        self.assertFalse(self.work.exists())
        self.assertFalse((self.rec / ".recording").exists(), "the lock outlived the recording")

    def test_a_late_refusal_leaves_the_meeting_now_recording_alone(self):
        # The dialog waits longer than the meeting can last. An answer that
        # lands afterwards must not stop whatever is recording by then.
        later = self.rec / "2026-09-04-1800-sdk-sync"
        later.mkdir()
        self.hold(later)
        self.discard()
        self.assertIsNone(self.recorder.poll(), "it stopped the next meeting's recorder")
        self.assertTrue((self.rec / ".recording").exists(), "it took the next meeting's lock")
        self.assertFalse(self.work.exists(), "the refused audio survived")


class QuittingHonoursTheRefusal(unittest.TestCase):
    """Ctrl-C must not turn "do not record" into a saved recording.

    watch_cleanup never read the consent, so quitting kept the audio and
    printed an invitation to transcribe it. Found on disk: a refused call with
    23 MB of audio and a `qn redo` suggestion beside it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.work = self.rec / "2026-09-05-1500-sdk-standup"
        self.work.mkdir()
        (self.work / "me.m4a").write_bytes(b"audio")
        self.recorder = subprocess.Popen(["sleep", "60"])
        self.addCleanup(self.recorder.wait)
        self.addCleanup(self.recorder.terminate)
        (self.rec / ".recording").write_text(str(self.work))
        (self.rec / ".recording.pid").write_text(str(self.recorder.pid))

    def quit_watch(self, consent):
        (self.work / "consent").write_text(consent + "\n")
        done = subprocess.run(["bash", "-c", watch_cleanup_script(self.rec)],
                              capture_output=True, text=True)
        return done.stdout + done.stderr

    def test_a_refused_recording_is_deleted_on_the_way_out(self):
        said = self.quit_watch("none")
        self.assertFalse(self.work.exists(), "quitting kept audio the user refused")
        self.assertNotIn("qn redo", said, "it offered to transcribe a refusal")
        self.assertIn("the audio is gone", said)

    def test_a_kept_recording_is_still_saved_and_named(self):
        said = self.quit_watch("full")
        self.assertTrue(self.work.exists())
        self.assertIn("qn redo 2026-09-05-1500-sdk-standup", said)

    def test_an_unanswered_dialog_keeps_the_recording(self):
        # read_consent fails closed to `local`. Only an explicit refusal deletes.
        said = self.quit_watch("")
        self.assertTrue(self.work.exists(), "silence was read as a refusal")
        self.assertIn("qn redo", said)

    def test_a_recorder_that_will_not_die_does_not_hold_the_watch(self):
        # The loop cannot read its own event pipe while this waits, so the wait
        # is bounded. It used to be a plain `wait` with no limit at all.
        stubborn = subprocess.Popen(["bash", "-c", 'trap "" TERM; sleep 30'])
        self.addCleanup(stubborn.wait)
        self.addCleanup(stubborn.kill)
        (self.rec / ".recording.pid").write_text(str(stubborn.pid))
        started = time.monotonic()
        self.quit_watch("full")
        self.assertLess(time.monotonic() - started, 5, "a stuck recorder held the watch")
        self.assertIsNone(stubborn.poll(), "it died after all — test proves nothing")

    def test_the_recorder_is_stopped_and_reaped_either_way(self):
        self.quit_watch("full")
        self.assertIsNotNone(self.recorder.poll(), "the recorder outlived the watch")
        self.assertFalse((self.rec / ".recording").exists())


if __name__ == "__main__":
    unittest.main()
