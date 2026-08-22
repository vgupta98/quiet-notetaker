"""Tests for the shell helpers and dispatch in `qn`.

These exist because every bug they cover shipped with a green suite. The pure
helpers are extracted from the script itself, so a rename breaks the test
rather than silently skipping it.
"""

import os
import pathlib
import re
import subprocess
import tempfile
import unittest

QN = pathlib.Path(__file__).parent / "qn"


def call_helper(name: str, *args: str) -> str:
    """Run one bash function out of `qn`, with nothing else loaded."""
    source = QN.read_text()
    match = re.search(rf"^{re.escape(name)}\(\) \{{.*?^\}}", source, re.S | re.M)
    if match is None:
        raise AssertionError(f"{name}() no longer exists in qn — update this test")
    script = f'{match.group(0)}\n{name} "$@"'
    done = subprocess.run(["bash", "-c", script, "_", *args], capture_output=True, text=True)
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

    def test_a_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_qn("some meeting", notes=tmp)
            self.assertEqual(os.listdir(tmp), [])


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
