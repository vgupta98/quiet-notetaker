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
    """A meeting whose name starts with a subcommand must still record."""

    def test_a_title_beginning_with_a_verb_records(self):
        for title in (
            ["index", "review", "with", "priya"],
            ["watch", "party", "planning"],
            ["play", "testing", "session"],
            ["doctor", "sync"],
            ["pending", "items", "review"],
        ):
            done = run_qn(*title)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("id=", done.stdout, f"{title} did not record")

    def test_a_bare_verb_still_runs_the_subcommand(self):
        done = run_qn("doctor")
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
        done = run_qn("--notes-only", "some meeting")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("--notes-only", done.stderr)
        self.assertNotIn("id=", done.stdout)

    def test_a_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_qn("some meeting", notes=tmp)
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


if __name__ == "__main__":
    unittest.main()
