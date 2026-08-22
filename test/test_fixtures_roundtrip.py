#!/usr/bin/env python3
"""Round-trip the fixture corpus through the real index.

`fixtures.build_corpus` returns the ground truth for every meeting it wrote.
This test feeds those same files to `mcp/index.py` and checks that what the
index reports back matches that ground truth, item for item: how many actions,
which are done, who owns them, and whose they are.

Nothing here re-implements the parser. When `index.py` and `fixtures.py`
disagree about the note format, this test fails, which is the point.

Stdlib only.
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (HERE, os.path.join(ROOT, "mcp")):
    if path not in sys.path:
        sys.path.insert(0, path)

import fixtures  # noqa: E402
import index  # noqa: E402

#: Big enough to cover the hand-written meetings and the generated extras.
CORPUS_COUNT = 14

#: Higher than any corpus this test builds, so nothing is cut off.
NO_LIMIT = 100000


def _key(meeting_id, item):
    """One action item as a comparable tuple."""
    return (meeting_id, item["whose"], item["owner"], item["text"],
            bool(item["done"]))


class FixtureRoundTrip(unittest.TestCase):
    """Build a corpus, index it, and compare the index against the fixtures."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.mkdtemp(prefix="qn-fixture-roundtrip.")
        cls.meetings = fixtures.build_corpus(cls.directory, count=CORPUS_COUNT)
        cls.db_path = index.index_path(cls.directory)
        cls.stats = index.refresh(cls.db_path, cls.directory, rebuild=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    # -- helpers ----------------------------------------------------------

    def expected(self, status="all", whose="all"):
        """Ground-truth action items, as a sorted list of tuples."""
        wanted = []
        for meeting in self.meetings:
            if meeting["sharing"] != "full":
                continue
            for item in meeting["actions"]:
                if status == "open" and item["done"]:
                    continue
                if status == "done" and not item["done"]:
                    continue
                if whose != "all" and item["whose"] != whose:
                    continue
                wanted.append(_key(meeting["id"], item))
        return sorted(wanted)

    def reported(self, status="all", whose="all"):
        """What the index reports, as a sorted list of the same tuples."""
        result = index.actions(self.db_path, status=status, whose=whose,
                               limit=NO_LIMIT)
        self.assertEqual(result["total"], len(result["items"]))
        return sorted(_key(item["id"], item) for item in result["items"])

    # -- tests ------------------------------------------------------------

    def test_corpus_has_both_kinds_of_meeting(self):
        """The comparison is only worth running on a mixed corpus."""
        sharings = set(meeting["sharing"] for meeting in self.meetings)
        self.assertEqual(sharings, {"full", "local"})
        self.assertTrue(any(meeting["actions"] for meeting in self.meetings))

    def test_indexed_count_matches_the_shareable_meetings(self):
        shareable = [m for m in self.meetings if m["sharing"] == "full"]
        self.assertEqual(self.stats["total"], len(shareable))

    def test_every_action_item_round_trips(self):
        self.assertEqual(self.reported(), self.expected())

    def test_open_and_done_split(self):
        self.assertEqual(self.reported(status="open"),
                         self.expected(status="open"))
        self.assertEqual(self.reported(status="done"),
                         self.expected(status="done"))
        self.assertEqual(
            len(self.expected(status="open")) + len(self.expected(status="done")),
            len(self.expected()))

    def test_mine_and_theirs_split(self):
        self.assertEqual(self.reported(whose="mine"),
                         self.expected(whose="mine"))
        self.assertEqual(self.reported(whose="theirs"),
                         self.expected(whose="theirs"))
        self.assertTrue(self.expected(whose="mine"))
        self.assertTrue(self.expected(whose="theirs"))

    def test_owner_extraction(self):
        """An owner prefix is read back as an owner, and stripped from the text."""
        owners = {}
        for meeting in self.meetings:
            for item in meeting["actions"]:
                owners[(meeting["id"], item["whose"], item["text"])] = item["owner"]

        result = index.actions(self.db_path, status="all", whose="all",
                               limit=NO_LIMIT)
        checked = 0
        for item in result["items"]:
            key = (item["id"], item["whose"], item["text"])
            self.assertIn(key, owners, "the index invented an action item")
            self.assertEqual(item["owner"], owners[key])
            if owners[key] is not None:
                checked += 1
                self.assertNotIn(":", item["text"].split(" ")[0])
        self.assertTrue(checked, "no owned item in the corpus to check")

    def test_mine_never_carries_an_owner(self):
        result = index.actions(self.db_path, status="all", whose="mine",
                               limit=NO_LIMIT)
        for item in result["items"]:
            self.assertIsNone(item["owner"])

    def test_local_meetings_are_absent(self):
        for meeting in self.meetings:
            if meeting["sharing"] != "full":
                with self.assertRaises(LookupError):
                    index.get(self.db_path, meeting["id"])

    def test_no_action_comes_from_a_local_meeting(self):
        local_ids = set(meeting["id"] for meeting in self.meetings
                        if meeting["sharing"] != "full")
        self.assertTrue(local_ids)
        result = index.actions(self.db_path, status="all", whose="all",
                               limit=NO_LIMIT)
        for item in result["items"]:
            self.assertNotIn(item["id"], local_ids)

    def test_titles_and_attendees_round_trip(self):
        for meeting in self.meetings:
            if meeting["sharing"] != "full":
                continue
            found = index.get(self.db_path, meeting["id"])
            self.assertEqual(found["title"], meeting["title"])
            self.assertEqual(found["attendees"], meeting["attendees"])
            self.assertEqual(found["date"], meeting["date"])
            self.assertEqual(found["sharing"], "full")

    def test_sections_round_trip(self):
        for meeting in self.meetings:
            if meeting["sharing"] != "full":
                continue
            found = index.get(self.db_path, meeting["id"])
            self.assertEqual(list(found["sections"]), fixtures.SECTION_NAMES)


if __name__ == "__main__":
    unittest.main()
