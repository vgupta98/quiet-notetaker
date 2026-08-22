#!/usr/bin/env python3
"""Tests for the meeting index. Standard library only.

Every test builds its own notes directory in a temporary folder, so the tests
never read the real ~/Meetings and never depend on another file in this repo.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import index  # noqa: E402

FENCE = "```"

SDK_SYNC = f"""---
title: "SDK Sync"
date: 2026-08-20 15:35
attendees: ["Priya", "Arjun"]
sharing: full
capture: ok
warnings: []
---

# SDK Sync

_20 Aug 2026, 15:35_ · _With: Priya, Arjun_

## Summary
- We settled the retry budget for the mobile sdk.

## Decisions
- Ship the batching change next week.

## My action items
- [ ] Write the migration guide
- [x] Send the roadmap out

## Their action items
- [ ] Priya: review the retry budget
- [x] Arjun: publish the snapshot
- [ ] Them: confirm the release date

## Open questions
- Does the flush interval need a cap?

---

## Transcript

{FENCE}
[00:05] Me: the kestrel dashboard fell over again
[01:37] Them: I will look at the retry budget
[02:40] Them: and the kestrel alerts are noisy
[104:12] Me: we can ship batching next week
{FENCE}
"""

HELD_NOTE = f"""---
title: "Held Chat"
date: 2026-08-21 09:00
attendees: ["Priya"]
sharing: local
capture: ok
warnings: []
---

# Held Chat

_21 Aug 2026, 09:00_

## My action items
- [ ] hushword the salary review

---

## Transcript

{FENCE}
[00:02] Me: hushword, this one stays on my mac
[00:09] Them: the kestrel budget again
{FENCE}
"""

# Older, no attendees, and two frontmatter keys left out on purpose.
OLD_ROADMAP = f"""---
title: "Old Roadmap"
date: 2026-05-04 10:00
attendees: []
sharing: full
---

# Old Roadmap

## Summary
- The quarterly roadmap for the mobile sdk.

## My action items
- [x] Book the roadmap review

## Their action items
- None

---

## Transcript

{FENCE}
[00:11] Them: the roadmap needs a kestrel line item
{FENCE}
"""


def write_note(directory: str, meeting_id: str, text: str, *, age: float = 0.0) -> str:
    """Write one note. `age` backdates the mtime so refresh sees a stable file."""
    path = os.path.join(directory, f"{meeting_id}.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    if age:
        stamp = os.stat(path).st_mtime - age
        os.utime(path, (stamp, stamp))
    return path


class IndexTestCase(unittest.TestCase):
    """Builds the standard three-note corpus and indexes it."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.notes = self._temporary.name
        self.db = index.index_path(self.notes)
        write_note(self.notes, "2026-08-20-1535-sdk-sync", SDK_SYNC)
        write_note(self.notes, "2026-08-21-0900-held-chat", HELD_NOTE)
        write_note(self.notes, "2026-05-04-1000-old-roadmap", OLD_ROADMAP)
        index.refresh(self.db, self.notes)
        self.addCleanup(self._temporary.cleanup)


class FrontmatterTests(unittest.TestCase):
    def test_reads_scalars_and_lists(self) -> None:
        fields, body = index.parse_frontmatter(SDK_SYNC)
        self.assertEqual(fields["title"], "SDK Sync")
        self.assertEqual(fields["date"], "2026-08-20 15:35")
        self.assertEqual(fields["attendees"], ["Priya", "Arjun"])
        self.assertEqual(fields["sharing"], "full")
        self.assertTrue(body.lstrip().startswith("# SDK Sync"))

    def test_empty_list_and_missing_keys(self) -> None:
        fields, _ = index.parse_frontmatter(OLD_ROADMAP)
        self.assertEqual(fields["attendees"], [])
        self.assertNotIn("capture", fields)
        self.assertNotIn("warnings", fields)

    def test_quoted_comma_stays_in_one_item(self) -> None:
        fields, _ = index.parse_frontmatter(
            '---\nwarnings: ["mic was silent, check input", "short"]\n---\nbody\n'
        )
        self.assertEqual(fields["warnings"], ["mic was silent, check input", "short"])

    def test_note_without_frontmatter_keeps_its_body(self) -> None:
        fields, body = index.parse_frontmatter("# Plain\n\n## Summary\n- hello\n")
        self.assertEqual(fields, {})
        self.assertTrue(body.startswith("# Plain"))

    def test_missing_frontmatter_falls_back_to_the_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_note(directory, "2026-01-02-0930-plain-talk", "# Plain Talk\n\n## Summary\n- hi\n")
            note = index.parse_note(path)
            self.assertIsNotNone(note)
            self.assertEqual(note["title"], "Plain Talk")
            self.assertEqual(note["date"], "2026-01-02 09:30")
            self.assertEqual(note["sharing"], "full")


class NoteSplittingTests(unittest.TestCase):
    def test_sections_split_on_headings(self) -> None:
        _, body = index.parse_frontmatter(SDK_SYNC)
        notes_text, _ = index.split_note(body)
        sections = index.split_sections(notes_text)
        self.assertEqual(
            list(sections),
            ["Summary", "Decisions", "My action items", "Their action items", "Open questions"],
        )
        self.assertIn("batching change", sections["Decisions"])
        self.assertNotIn("Transcript", sections)

    def test_transcript_comes_from_the_fenced_block(self) -> None:
        _, body = index.parse_frontmatter(SDK_SYNC)
        notes_text, transcript_text = index.split_note(body)
        lines = transcript_text.split("\n")
        self.assertEqual(lines[0], "[00:05] Me: the kestrel dashboard fell over again")
        self.assertEqual(lines[-1], "[104:12] Me: we can ship batching next week")
        self.assertNotIn(FENCE, transcript_text)
        self.assertNotIn("kestrel", notes_text)
        self.assertFalse(notes_text.rstrip().endswith("---"))

    def test_note_without_a_transcript(self) -> None:
        notes_text, transcript_text = index.split_note("# Only\n\n## Summary\n- one line\n")
        self.assertEqual(transcript_text, "")
        self.assertIn("one line", notes_text)


class ActionParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        _, body = index.parse_frontmatter(SDK_SYNC)
        notes_text, _ = index.split_note(body)
        self.items = index.parse_actions(index.split_sections(notes_text))

    def test_mine_and_theirs_are_separated(self) -> None:
        self.assertEqual([item["whose"] for item in self.items][:2], ["mine", "mine"])
        self.assertEqual({item["whose"] for item in self.items}, {"mine", "theirs"})

    def test_done_and_open_are_read_from_the_box(self) -> None:
        by_text = {item["text"]: item["done"] for item in self.items}
        self.assertFalse(by_text["Write the migration guide"])
        self.assertTrue(by_text["Send the roadmap out"])

    def test_owner_comes_from_the_name_before_the_colon(self) -> None:
        by_text = {item["text"]: item["owner"] for item in self.items}
        self.assertEqual(by_text["review the retry budget"], "Priya")
        self.assertEqual(by_text["publish the snapshot"], "Arjun")
        self.assertEqual(by_text["confirm the release date"], "Them")
        self.assertIsNone(by_text["Write the migration guide"])

    def test_a_sentence_with_a_colon_has_no_owner(self) -> None:
        sections = {"My action items": "- [ ] Rewrite the docs page: it says the wrong thing"}
        item = index.parse_actions(sections)[0]
        self.assertIsNone(item["owner"])
        self.assertEqual(item["text"], "Rewrite the docs page: it says the wrong thing")

    def test_prose_bullets_are_not_actions(self) -> None:
        self.assertEqual(index.parse_actions({"Summary": "- not an action"}), [])
        self.assertEqual(index.parse_actions({"Their action items": "- None"}), [])


class SharingTests(IndexTestCase):
    def test_a_local_note_is_never_indexed(self) -> None:
        connection = sqlite3.connect(self.db)
        rows = [row[0] for row in connection.execute("SELECT id FROM meetings")]
        connection.close()
        self.assertNotIn("2026-08-21-0900-held-chat", rows)
        self.assertEqual(len(rows), 2)

    def test_a_local_note_never_appears_in_search(self) -> None:
        for query in ("hushword", "kestrel"):
            results = index.search(self.db, query, limit=50)["results"]
            self.assertNotIn("2026-08-21-0900-held-chat", [hit["id"] for hit in results])

    def test_a_local_note_never_appears_in_actions(self) -> None:
        found = index.actions(self.db, status="all", whose="all", limit=100)["items"]
        self.assertNotIn("2026-08-21-0900-held-chat", [item["id"] for item in found])
        self.assertNotIn("hushword the salary review", [item["text"] for item in found])

    def test_a_note_turned_local_leaves_the_index(self) -> None:
        write_note(self.notes, "2026-08-20-1535-sdk-sync", SDK_SYNC.replace("sharing: full", "sharing: local"))
        index.refresh(self.db, self.notes)
        self.assertEqual(index.search(self.db, "retry budget")["total"], 0)
        with self.assertRaises(LookupError):
            index.get(self.db, "2026-08-20-1535-sdk-sync")


class RefreshTests(IndexTestCase):
    def test_an_edit_is_picked_up(self) -> None:
        self.assertEqual(index.search(self.db, "porcupine")["total"], 0)
        write_note(
            self.notes,
            "2026-08-20-1535-sdk-sync",
            SDK_SYNC.replace("Ship the batching change", "Ship the porcupine change"),
        )
        stats = index.refresh(self.db, self.notes)
        self.assertEqual(stats["indexed"], 1)
        self.assertEqual(index.search(self.db, "porcupine")["total"], 1)
        # The word left the notes, so only the transcript still carries it.
        self.assertEqual(index.search(self.db, "batching")["results"][0]["matched_in"], "transcript")

    def test_an_untouched_note_is_not_reindexed(self) -> None:
        stats = index.refresh(self.db, self.notes)
        self.assertEqual(stats["indexed"], 0)
        self.assertEqual(stats["removed"], 0)
        self.assertEqual(stats["total"], 2)

    def test_a_deleted_note_disappears(self) -> None:
        os.remove(os.path.join(self.notes, "2026-08-20-1535-sdk-sync.md"))
        stats = index.refresh(self.db, self.notes)
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(index.search(self.db, "retry budget")["total"], 0)
        self.assertEqual(
            index.actions(self.db, status="all", whose="all")["total"],
            1,  # only the old roadmap's one item is left
        )

    def test_a_rebuild_matches_the_incremental_index(self) -> None:
        incremental = self._dump(self.db)
        rebuilt_db = os.path.join(self.notes, "rebuilt.db")
        index.refresh(rebuilt_db, self.notes, rebuild=True)
        self.assertEqual(incremental, self._dump(rebuilt_db))

    def test_a_rebuild_of_the_same_file_keeps_the_rows(self) -> None:
        before = self._dump(self.db)
        index.refresh(self.db, self.notes, rebuild=True)
        self.assertEqual(before, self._dump(self.db))

    @staticmethod
    def _dump(db_path: str) -> tuple:
        connection = sqlite3.connect(db_path)
        meetings = sorted(connection.execute(
            "SELECT id, title, date, attendees, sharing, notes_text, transcript_text FROM meetings"
        ))
        found = sorted(connection.execute("SELECT meeting_id, whose, owner, text, done FROM actions"))
        connection.close()
        return meetings, found


class SearchTests(IndexTestCase):
    def test_matched_in_is_notes_for_a_summary_hit(self) -> None:
        hit = index.search(self.db, "retry budget")["results"][0]
        self.assertEqual(hit["id"], "2026-08-20-1535-sdk-sync")
        self.assertEqual(hit["matched_in"], "notes")
        self.assertEqual(hit["attendees"], ["Priya", "Arjun"])
        self.assertIn("retry budget", hit["snippet"])

    def test_matched_in_is_transcript_when_only_the_transcript_has_it(self) -> None:
        results = index.search(self.db, "kestrel", limit=10)["results"]
        self.assertEqual({hit["matched_in"] for hit in results}, {"transcript"})
        self.assertIn("kestrel", results[0]["snippet"])

    def test_a_word_in_both_fields_counts_as_a_notes_hit(self) -> None:
        results = index.search(self.db, "batching")["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["matched_in"], "notes")

    def test_results_never_carry_whole_documents(self) -> None:
        hit = index.search(self.db, "kestrel")["results"][0]
        self.assertEqual(set(hit), {"id", "title", "date", "attendees", "matched_in", "snippet"})
        self.assertLess(len(hit["snippet"]), 300)

    def test_date_range_filters(self) -> None:
        every = index.search(self.db, "mobile sdk", limit=50)
        self.assertEqual(every["total"], 2)
        recent = index.search(self.db, "mobile sdk", from_="2026-08-01", limit=50)
        self.assertEqual([hit["id"] for hit in recent["results"]], ["2026-08-20-1535-sdk-sync"])
        older = index.search(self.db, "mobile sdk", to="2026-06-01", limit=50)
        self.assertEqual([hit["id"] for hit in older["results"]], ["2026-05-04-1000-old-roadmap"])
        none_left = index.search(self.db, "mobile sdk", from_="2026-06-01", to="2026-07-01")
        self.assertEqual(none_left["total"], 0)

    def test_attendee_filter(self) -> None:
        self.assertEqual(index.search(self.db, "mobile sdk", with_="priya")["total"], 1)
        self.assertEqual(index.search(self.db, "mobile sdk", with_="nobody")["total"], 0)

    def test_odd_query_syntax_does_not_raise(self) -> None:
        # An unbalanced quote is not FTS5 syntax; it is read as plain words.
        self.assertEqual(index.search(self.db, 'retry "budget')["total"], 1)
        self.assertEqual(index.search(self.db, "AND OR")["shown"], index.search(self.db, "AND OR")["total"])
        self.assertEqual(index.search(self.db, "zzzznothing")["total"], 0)

    def test_an_empty_query_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            index.search(self.db, "   ")


class SearchCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.notes = self._temporary.name
        self.db = index.index_path(self.notes)
        for number in range(12):
            write_note(
                self.notes,
                f"2026-03-{number + 1:02d}-1000-standup-{number}",
                f"---\ntitle: \"Standup {number}\"\ndate: 2026-03-{number + 1:02d} 10:00\n"
                f"attendees: []\nsharing: full\n---\n\n## Summary\n- the widget review, part {number}\n",
            )
        index.refresh(self.db, self.notes)
        self.addCleanup(self._temporary.cleanup)

    def test_total_counts_every_hit_and_shown_counts_the_capped_page(self) -> None:
        capped = index.search(self.db, "widget", limit=5)
        self.assertEqual(capped["total"], 12)
        self.assertEqual(capped["shown"], 5)
        self.assertEqual(len(capped["results"]), 5)

    def test_the_default_limit_is_ten(self) -> None:
        self.assertEqual(index.search(self.db, "widget")["shown"], 10)

    def test_a_limit_above_the_corpus_shows_everything(self) -> None:
        wide = index.search(self.db, "widget", limit=100)
        self.assertEqual((wide["total"], wide["shown"]), (12, 12))


class GetTests(IndexTestCase):
    def test_returns_the_notes_and_never_the_transcript(self) -> None:
        meeting = index.get(self.db, "2026-08-20-1535-sdk-sync")
        self.assertEqual(meeting["title"], "SDK Sync")
        self.assertEqual(meeting["attendees"], ["Priya", "Arjun"])
        self.assertEqual(meeting["sharing"], "full")
        self.assertIn("Decisions", meeting["sections"])
        self.assertNotIn("Transcript", meeting["sections"])
        self.assertNotIn("kestrel", "\n".join(meeting["sections"].values()))

    def test_one_section_can_be_asked_for(self) -> None:
        meeting = index.get(self.db, "2026-08-20-1535-sdk-sync", section="decisions")
        self.assertEqual(list(meeting["sections"]), ["Decisions"])

    def test_an_unknown_id_raises(self) -> None:
        with self.assertRaises(LookupError):
            index.get(self.db, "2026-01-01-0000-nothing")

    def test_an_unknown_section_raises(self) -> None:
        with self.assertRaises(LookupError):
            index.get(self.db, "2026-08-20-1535-sdk-sync", section="Budget")


class TranscriptTests(IndexTestCase):
    def test_returns_every_line(self) -> None:
        found = index.transcript(self.db, "2026-08-20-1535-sdk-sync")
        self.assertEqual(len(found["lines"]), 4)
        self.assertFalse(found["truncated"])
        self.assertEqual(found["title"], "SDK Sync")

    def test_around_keeps_only_nearby_lines(self) -> None:
        near = index.transcript(self.db, "2026-08-20-1535-sdk-sync", around="01:37", window=60)
        self.assertEqual(near["lines"], ["[01:37] Them: I will look at the retry budget"])
        self.assertTrue(near["truncated"])

        wider = index.transcript(self.db, "2026-08-20-1535-sdk-sync", around="01:37", window=100)
        self.assertEqual(
            wider["lines"],
            [
                "[00:05] Me: the kestrel dashboard fell over again",
                "[01:37] Them: I will look at the retry budget",
                "[02:40] Them: and the kestrel alerts are noisy",
            ],
        )

    def test_minutes_may_exceed_fifty_nine(self) -> None:
        found = index.transcript(self.db, "2026-08-20-1535-sdk-sync", around="104:12", window=10)
        self.assertEqual(found["lines"], ["[104:12] Me: we can ship batching next week"])

    def test_a_bad_timestamp_raises(self) -> None:
        with self.assertRaises(ValueError):
            index.transcript(self.db, "2026-08-20-1535-sdk-sync", around="soon")

    def test_an_unknown_id_raises(self) -> None:
        with self.assertRaises(LookupError):
            index.transcript(self.db, "2026-01-01-0000-nothing")


class ActionQueryTests(IndexTestCase):
    def test_open_items_of_mine_by_default(self) -> None:
        found = index.actions(self.db)
        self.assertEqual([item["text"] for item in found["items"]], ["Write the migration guide"])
        self.assertEqual((found["total"], found["shown"]), (1, 1))

    def test_theirs_carries_the_owner(self) -> None:
        found = index.actions(self.db, whose="theirs", status="all")
        owners = {item["text"]: item["owner"] for item in found["items"]}
        self.assertEqual(owners["review the retry budget"], "Priya")
        self.assertEqual(owners["publish the snapshot"], "Arjun")

    def test_done_only(self) -> None:
        found = index.actions(self.db, status="done", whose="all")
        self.assertTrue(all(item["done"] for item in found["items"]))
        self.assertEqual(len(found["items"]), 3)

    def test_items_name_their_meeting(self) -> None:
        item = index.actions(self.db, status="all", whose="all")["items"][0]
        self.assertEqual(item["id"], "2026-08-20-1535-sdk-sync")
        self.assertEqual(item["meeting_title"], "SDK Sync")

    def test_date_range_filter(self) -> None:
        found = index.actions(self.db, status="all", whose="all", to="2026-06-01")
        self.assertEqual([item["id"] for item in found["items"]], ["2026-05-04-1000-old-roadmap"])

    def test_limit_caps_the_page_but_not_the_total(self) -> None:
        found = index.actions(self.db, status="all", whose="all", limit=2)
        self.assertEqual(found["total"], 6)
        self.assertEqual(found["shown"], 2)
        self.assertEqual(len(found["items"]), 2)

    def test_bad_arguments_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            index.actions(self.db, status="maybe")
        with self.assertRaises(ValueError):
            index.actions(self.db, whose="ours")


class NotesDirTests(unittest.TestCase):
    def test_the_environment_variable_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("QN_NOTES_DIR")
            os.environ["QN_NOTES_DIR"] = directory
            try:
                self.assertEqual(index.notes_dir(), os.path.abspath(directory))
                self.assertEqual(index.index_path(), os.path.join(os.path.abspath(directory), ".index.db"))
            finally:
                if previous is None:
                    del os.environ["QN_NOTES_DIR"]
                else:
                    os.environ["QN_NOTES_DIR"] = previous


if __name__ == "__main__":
    unittest.main()
