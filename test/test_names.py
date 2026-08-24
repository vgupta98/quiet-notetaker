#!/usr/bin/env python3
"""Tests for names.py — reading a person out of a calendar invite."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib"), os.path.join(ROOT, "mcp")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import unittest

import names


class LooksLikeEmail(unittest.TestCase):
    def test_an_address(self):
        self.assertTrue(names.looks_like_email("aisha@example.com"))

    def test_a_name(self):
        self.assertFalse(names.looks_like_email("Priya Sharma"))

    def test_a_name_with_an_at_sign_but_no_dot(self):
        self.assertFalse(names.looks_like_email("aisha@localhost"))

    def test_a_numeric_last_label_is_not_a_domain(self):
        self.assertFalse(names.looks_like_email("thing@10.0.0.1"))

    def test_whitespace_is_ignored(self):
        self.assertTrue(names.looks_like_email("  aisha@example.com  "))


class DisplayName(unittest.TestCase):
    def test_a_plain_address_becomes_a_first_name(self):
        self.assertEqual(names.display_name("aisha@example.com"), "Aisha")

    def test_a_dotted_local_part_becomes_two_names(self):
        self.assertEqual(
            names.display_name("aisha.khan@example.com"), "Aisha Khan")

    def test_underscores_and_hyphens_split_too(self):
        self.assertEqual(names.display_name("priya_sharma@x.com"), "Priya Sharma")
        self.assertEqual(names.display_name("priya-sharma@x.com"), "Priya Sharma")

    def test_a_plus_tag_is_routing_not_a_name(self):
        self.assertEqual(names.display_name("sam+notes@mail.example.co.uk"), "Sam")

    def test_a_name_someone_typed_is_left_alone(self):
        """People write their colleagues' names the way they want them."""
        self.assertEqual(names.display_name("Priya Sharma"), "Priya Sharma")
        self.assertEqual(names.display_name("SDK Engg"), "SDK Engg")

    def test_mixed_case_is_preserved(self):
        """`.capitalize()` turns McDonald into Mcdonald, which is worse."""
        self.assertEqual(names.display_name("McDonald@x.io"), "McDonald")
        self.assertEqual(names.display_name("deSouza@x.io"), "deSouza")

    def test_digits_alone_are_dropped(self):
        self.assertEqual(names.display_name("priya.2@x.com"), "Priya")

    def test_a_local_part_of_only_digits_falls_back_to_the_raw_string(self):
        self.assertEqual(names.display_name("12345@x.com"), "12345@x.com")

    def test_empty_stays_empty(self):
        self.assertEqual(names.display_name(""), "")

    def test_whitespace_is_trimmed(self):
        self.assertEqual(names.display_name("  Priya Sharma  "), "Priya Sharma")


class VocabularySource(unittest.TestCase):
    def test_the_top_level_domain_is_dropped(self):
        """"com" primes whisper for nothing and costs a slot."""
        self.assertEqual(
            names.vocabulary_source("aisha@example.com"), "aisha example")

    def test_a_two_part_suffix_leaves_only_the_company(self):
        """`co` survives here, then vocab.py drops it for being under three."""
        self.assertEqual(
            names.vocabulary_source("sam@example.co.uk"), "sam example co")

    def test_a_subdomain_is_kept(self):
        self.assertEqual(
            names.vocabulary_source("sam@mail.example.com"), "sam mail example")

    def test_a_name_is_returned_unchanged(self):
        self.assertEqual(names.vocabulary_source("Priya Sharma"), "Priya Sharma")


class ThroughTheRoster(unittest.TestCase):
    """The two modules that read attendees must agree with names.py."""

    def test_the_roster_names_a_person_not_an_address(self):
        import people
        found = people.harvest([{
            "id": "2026-01-01-1000-a",
            "date": "2026-01-01 10:00",
            "attendees": ["aisha@example.com"],
        }])
        self.assertEqual([person.name for person in found], ["Aisha"])

    def test_an_address_in_with_matches_the_roster_entry(self):
        """Without this, context() looks up a name the roster never stored."""
        import people
        self.assertEqual(
            people.split_attendees("aisha@example.com"), ["Aisha"])
        self.assertTrue(people.same_person("Aisha", "aisha@example.com"))

    def test_a_roster_written_before_this_fix_reads_back_as_a_name(self):
        """An old people.md holding an address needs no migration step."""
        import people
        parsed = people.parse_roster("- **aisha@example.com** (1 meeting, last 2026-01-01)")
        self.assertEqual(parsed[0].name, "Aisha")

    def test_the_vocabulary_learns_the_person_and_the_company_but_no_tld(self):
        import vocab
        learned = vocab.harvest([{
            "id": "2026-01-01-1000-a",
            "attendees": ["aisha@example.com"],
            "transcript_text": "",
            "notes_text": "",
        }])
        folded = {term.lower() for term in learned}
        self.assertIn("aisha", folded)
        self.assertIn("example", folded)
        self.assertNotIn("com", folded)


if __name__ == "__main__":
    unittest.main()
