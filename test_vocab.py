"""Tests for the vocabulary loop.

The important ones are in PoisoningGuards. A vocabulary that learns from the
decoder's own output reinforces its own mistakes, so these assert that the
path simply does not exist.
"""

import os
import tempfile
import unittest

import vocab


def note(meeting_id, notes_text="", transcript_text="", attendees=()):
    return {
        "id": meeting_id,
        "attendees": list(attendees),
        "notes_text": notes_text,
        "transcript_text": transcript_text,
    }


class InterestingShapes(unittest.TestCase):
    def test_keeps_domain_terms(self):
        for word in ("on-call", "Amplitude", "RudderStack", "iOS", "Braze", "device-mode"):
            self.assertTrue(vocab.is_interesting(word), word)

    def test_rejects_ordinary_english(self):
        for word in ("agreed", "practice", "the", "meeting", "we", "it"):
            self.assertFalse(vocab.is_interesting(word), word)

    def test_rejects_sentence_starters_even_capitalised(self):
        self.assertFalse(vocab.is_interesting("The"))
        self.assertFalse(vocab.is_interesting("When"))

    def test_rejects_very_short_words(self):
        self.assertFalse(vocab.is_interesting("Go"))


class Harvest(unittest.TestCase):
    def test_attendees_are_trusted_from_one_meeting(self):
        # Names are typed or read from a calendar. The decoder never made them
        # up, so there is nothing to corroborate.
        chosen = vocab.harvest([note("m1", attendees=["Priya Sharma"])])
        self.assertIn("Priya", chosen)
        self.assertIn("Sharma", chosen)

    def test_corrected_term_needs_two_meetings(self):
        one = [note("m1", notes_text="the on-call rota")]
        self.assertNotIn("on-call", vocab.harvest(one))

        two = one + [note("m2", notes_text="another on-call ticket")]
        self.assertIn("on-call", vocab.harvest(two))

    def test_ignores_private_meetings_by_construction(self):
        # parse_note returns None for sharing: local, so they never reach
        # harvest at all. Covered end to end in RefreshOnDisk.
        self.assertEqual(vocab.harvest([]), [])

    def test_respects_the_size_cap(self):
        many = [note(f"m{i}", attendees=[f"Aaa{i} Bbb{i}"]) for i in range(200)]
        self.assertLessEqual(len(vocab.harvest(many)), vocab.MAX_TERMS)


class PoisoningGuards(unittest.TestCase):
    """The loop must not be able to learn from the decoder's own output."""

    def test_a_term_whisper_produced_is_never_harvested(self):
        notes = [
            note("m1", notes_text="Vishal's uncle", transcript_text="Vishal's uncle"),
            note("m2", notes_text="the uncle ticket", transcript_text="the uncle ticket"),
        ]
        self.assertNotIn("uncle", [t.lower() for t in vocab.harvest(notes)])

    def test_one_transcript_disqualifies_the_term_everywhere(self):
        # Even if Claude "corrected" to the same wrong word in other meetings,
        # a single appearance in any transcript kills it.
        notes = [
            note("m1", notes_text="Tata-Geek call", transcript_text="the Tata-Geek call"),
            note("m2", notes_text="Tata-Geek call"),
            note("m3", notes_text="Tata-Geek call"),
        ]
        self.assertNotIn("tata-geek", [t.lower() for t in vocab.harvest(notes)])

    def test_runaway_simulation(self):
        """Feed the harvester its own output for ten rounds. It must not grow.

        This is the failure being guarded against: a mishearing enters the
        vocabulary, whisper is primed to produce it more, and the evidence for
        it appears to strengthen every round.
        """
        transcripts = ["Vishal's uncle rota", "the uncle handover", "next uncle person"]
        corpus = [
            note(f"m{i}", notes_text=f"{text} on-call", transcript_text=text)
            for i, text in enumerate(transcripts)
        ]

        previous = None
        for _ in range(10):
            chosen = vocab.harvest(corpus)
            folded = [t.lower() for t in chosen]
            self.assertNotIn("uncle", folded)
            # Simulate the primed decoder emitting the vocabulary back into the
            # transcripts, which is exactly how a real loop would contaminate.
            for entry in corpus:
                entry["transcript_text"] += " " + " ".join(chosen)
            if previous is not None:
                self.assertLessEqual(len(chosen), len(previous))
            previous = chosen

        # "on-call" was correct, but once the primed decoder starts producing
        # it, it stops needing a slot. Shrinking to nothing is the safe end.
        self.assertNotIn("uncle", [t.lower() for t in previous])


class RemovalsStick(unittest.TestCase):
    def test_a_deleted_term_is_never_re_added(self):
        removed = {"on-call"}
        merged = vocab.merge_with_existing([], ["on-call", "Amplitude"], removed)
        self.assertEqual(merged, ["Amplitude"])

    def test_user_additions_are_kept_and_come_first(self):
        merged = vocab.merge_with_existing(["MyOwnWord"], ["Amplitude"], set())
        self.assertEqual(merged, ["MyOwnWord", "Amplitude"])

    def test_duplicates_are_folded_case_insensitively(self):
        merged = vocab.merge_with_existing(["Amplitude"], ["amplitude"], set())
        self.assertEqual(merged, ["Amplitude"])


class RefreshOnDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def write_note(self, meeting_id, sharing, attendees, notes_body, transcript):
        text = (
            f"---\ntitle: \"{meeting_id}\"\ndate: 2026-08-20 10:00\n"
            f"attendees: [{', '.join(chr(34) + a + chr(34) for a in attendees)}]\n"
            f"sharing: {sharing}\ncapture: ok\nwarnings: []\n---\n\n"
            f"# {meeting_id}\n\n## Summary\n{notes_body}\n\n---\n\n"
            f"## Transcript\n\n```\n{transcript}\n```\n"
        )
        with open(os.path.join(self.dir, f"{meeting_id}.md"), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_private_meetings_contribute_nothing(self):
        self.write_note("2026-08-20-1000-secret", "local", ["Confidentia Personson"], "x", "y")
        self.assertEqual(vocab.refresh(self.dir), [])

    def test_full_meetings_contribute_attendees(self):
        self.write_note("2026-08-20-1000-sync", "full", ["Priya"], "x", "y")
        self.assertIn("Priya", vocab.refresh(self.dir))

    def test_deleting_a_line_keeps_it_out_on_the_next_refresh(self):
        self.write_note("2026-08-20-1000-sync", "full", ["Priya", "Arjun"], "x", "y")
        self.assertIn("Arjun", vocab.refresh(self.dir))

        kept = [t for t in vocab.read_vocabulary(self.dir) if t != "Arjun"]
        vocab._write(self.dir, kept)

        self.assertNotIn("Arjun", vocab.refresh(self.dir))
        self.assertNotIn("Arjun", vocab.refresh(self.dir))

    def test_prompt_is_a_plain_comma_list(self):
        self.assertEqual(vocab.as_prompt(["on-call", "Braze"]), "on-call, Braze")



class ScaffoldingIsNotVocabulary(unittest.TestCase):
    """The note template's own words must never be learned.

    Caught on real data: the first harvest returned "Summary", "Decisions" and
    "Aug", which are in every note ever written and so look maximally confirmed.
    """

    def test_headings_are_stripped(self):
        body = "# Sync\n\n## Summary\n- we shipped\n\n## Decisions\n- none\n"
        self.assertNotIn("Summary", vocab.strip_scaffolding(body))
        self.assertIn("we shipped", vocab.strip_scaffolding(body))

    def test_the_italic_meta_line_is_stripped(self):
        body = "_20 Aug 2026, 15:35_ · _With: Priya_\n\nreal content\n"
        self.assertNotIn("Aug", vocab.strip_scaffolding(body))

    def test_section_names_are_rejected_outright(self):
        for word in ("Summary", "Decisions", "Transcript", "Aug", "September", "Monday"):
            self.assertFalse(vocab.is_interesting(word), word)

    def test_template_words_never_reach_the_vocabulary(self):
        template = "# Sync\n\n_20 Aug 2026_\n\n## Summary\n- the on-call rota\n"
        chosen = vocab.harvest([note("m1", notes_text=template), note("m2", notes_text=template)])
        folded = [t.lower() for t in chosen]
        self.assertNotIn("summary", folded)
        self.assertNotIn("aug", folded)
        self.assertIn("on-call", folded)


class PositionMatters(unittest.TestCase):
    """A capital at the start of a bullet is grammar, not a proper noun.

    Caught on the fixture corpus: the first harvest learned "Write", "Drop",
    "Agreed" and "Ship" from action-item bullets.
    """

    def test_bullet_initial_verbs_are_rejected(self):
        found = vocab.terms_in("- [ ] Ship the release\n- Drop the bump\n- Agreed on Friday\n")
        self.assertNotIn("Ship", found)
        self.assertNotIn("Drop", found)
        self.assertNotIn("Agreed", found)

    def test_sentence_initial_words_are_rejected(self):
        self.assertNotIn("Review", vocab.terms_in("Review the doc. Then merge it."))

    def test_a_name_mid_sentence_is_kept(self):
        self.assertIn("Priya", vocab.terms_in("- [ ] Ask Priya about the rota"))

    def test_camel_case_survives_any_position(self):
        self.assertIn("RudderStack", vocab.terms_in("- RudderStack ships tomorrow"))

    def test_a_hyphenated_term_survives_any_position(self):
        self.assertIn("on-call", vocab.terms_in("- on-call handover happens Friday"))

    def test_numbered_lists_are_handled(self):
        self.assertNotIn("Send", vocab.terms_in("1. Send the invite"))


class OwnerPrefixesDoNotLeak(unittest.TestCase):
    """"Priya: Share the doc" capitalises "Share" by position, not by nature."""

    def test_a_word_after_an_owner_prefix_is_rejected(self):
        found = vocab.terms_in("- [ ] Priya: Share the doc with the team")
        self.assertNotIn("Share", found)

    def test_the_owner_name_itself_is_still_seen(self):
        self.assertIn("Priya", vocab.terms_in("- [ ] Priya: Share the doc"))

if __name__ == "__main__":
    unittest.main()
