#!/usr/bin/env python3
"""Tests for voices.py — the roster that carries a voice between meetings.

No models load here. The module is deliberately plain arithmetic so that the
decisions it makes can be tested with made-up vectors, which is where the
judgement lives.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _path in (HERE, os.path.join(ROOT, "lib")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import json
import tempfile
import unittest

import voices


def roster(**people):
    """A roster from `name=[(recording_id, letter, vector), ...]`."""
    return {name: [{"from": where, "voice": letter, "vector": list(vector)}
                   for where, letter, vector in entries]
            for name, entries in people.items()}


def best_score(people, vector):
    """The top score, ignoring the threshold that `match` applies."""
    ranked = voices.scores(people, vector)
    return ranked[0][1] if ranked else 0.0


class Loading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, text):
        with open(voices.roster_path(self.dir), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_no_file_is_an_empty_roster(self):
        self.assertEqual(voices.load(self.dir), {})

    def test_damaged_json_is_an_empty_roster(self):
        self.write("{not json at all")
        self.assertEqual(voices.load(self.dir), {})

    def test_a_list_where_an_object_belongs_is_an_empty_roster(self):
        self.write("[1, 2, 3]")
        self.assertEqual(voices.load(self.dir), {})

    def test_entries_without_a_vector_are_dropped(self):
        self.write(json.dumps({"version": 1, "people": {
            "Aisha": [{"from": "m1", "voice": "A", "vector": [1.0, 0.0]}, {"from": "m2"}]}}))
        self.assertEqual(voices.load(self.dir), roster(Aisha=[("m1", "A", [1.0, 0.0])]))

    def test_a_roster_written_before_voices_had_slots_still_loads(self):
        # Entries from the first version carry no `voice`. They stay usable,
        # and read as an empty letter, which collides with no real one.
        self.write(json.dumps({"version": 1, "people": {
            "Aisha": [{"from": "m1", "vector": [1.0, 0.0]}]}}))
        loaded = voices.load(self.dir)
        self.assertEqual(loaded["Aisha"][0]["vector"], [1.0, 0.0])
        self.assertEqual(voices._slot(loaded["Aisha"][0]), ("m1", ""))
        # And it still matches, which is what the roster is for.
        self.assertEqual(voices.match(loaded, [1.0, 0.0], 0.9), "Aisha")

    def test_a_person_with_no_usable_entry_disappears(self):
        self.write(json.dumps({"version": 1, "people": {"Ghost": [{"from": "m1"}]}}))
        self.assertEqual(voices.load(self.dir), {})

    def test_a_vector_of_strings_is_not_a_vector(self):
        self.write(json.dumps({"version": 1, "people": {
            "Aisha": [{"from": "m1", "vector": ["loud", "quiet"]}]}}))
        self.assertEqual(voices.load(self.dir), {})

    def test_save_then_load_returns_the_same_roster(self):
        original = roster(Aisha=[("m1", "A", [0.5, -0.25])], Marco=[("m2", "A", [0.125, 1.0])])
        voices.save(self.dir, original)
        self.assertEqual(voices.load(self.dir), original)

    def test_saving_an_empty_roster_leaves_a_readable_file(self):
        voices.save(self.dir, {})
        self.assertTrue(os.path.exists(voices.roster_path(self.dir)))
        self.assertEqual(voices.load(self.dir), {})


class Enrolling(unittest.TestCase):
    def test_a_new_person_joins_the_roster(self):
        after = voices.enrol({}, "Aisha", "m1", "A", [1.0, 0.0])
        self.assertEqual(after, roster(Aisha=[("m1", "A", [1.0, 0.0])]))

    def test_a_second_meeting_adds_a_second_entry(self):
        after = voices.enrol(roster(Aisha=[("m1", "A", [1.0, 0.0])]),
                             "Aisha", "m2", "A", [0.0, 1.0])
        self.assertEqual(len(after["Aisha"]), 2)

    def test_confirming_the_same_meeting_twice_replaces_it(self):
        after = voices.enrol(roster(Aisha=[("m1", "A", [1.0, 0.0])]),
                             "Aisha", "m1", "A", [0.0, 1.0])
        self.assertEqual(after, roster(Aisha=[("m1", "A", [0.0, 1.0])]))

    def test_correcting_a_name_takes_the_entry_from_the_wrong_person(self):
        # The whole point: a mistake must stop voting for whoever had it.
        before = roster(Aisha=[("m1", "A", [1.0, 0.0]), ("m2", "A", [0.9, 0.1])])
        after = voices.enrol(before, "Tom", "m1", "A", [1.0, 0.0])
        self.assertEqual(after["Aisha"], [{"from": "m2", "voice": "A", "vector": [0.9, 0.1]}])
        self.assertEqual(after["Tom"], [{"from": "m1", "voice": "A", "vector": [1.0, 0.0]}])

    def test_a_person_whose_only_entry_moved_away_is_gone(self):
        after = voices.enrol(roster(Aisha=[("m1", "A", [1.0, 0.0])]), "Tom", "m1", "A", [1.0, 0.0])
        self.assertNotIn("Aisha", after)

    def test_a_differently_cased_name_is_the_same_person(self):
        after = voices.enrol(roster(Aisha=[("m1", "A", [1.0, 0.0])]),
                             "aisha", "m2", "A", [0.0, 1.0])
        self.assertEqual(list(after), ["Aisha"])
        self.assertEqual(len(after["Aisha"]), 2)

    def test_an_empty_name_changes_nothing(self):
        before = roster(Aisha=[("m1", "A", [1.0, 0.0])])
        self.assertEqual(voices.enrol(before, "  ", "m2", "A", [0.0, 1.0]), before)

    def test_an_empty_vector_changes_nothing(self):
        before = roster(Aisha=[("m1", "A", [1.0, 0.0])])
        self.assertEqual(voices.enrol(before, "Tom", "m2", "A", []), before)

    def test_two_voices_from_one_meeting_are_two_samples(self):
        # The defect this replaced: the grouping splits one person across
        # letters, and keying by meeting alone threw the extra samples away.
        after = voices.enrol(roster(Aisha=[("m1", "A", [1.0, 0.0])]),
                             "Aisha", "m1", "B", [0.9, 0.1])
        self.assertEqual(len(after["Aisha"]), 2)
        self.assertEqual({entry["voice"] for entry in after["Aisha"]}, {"A", "B"})

    def test_correcting_one_letter_leaves_its_neighbour_alone(self):
        before = roster(Aisha=[("m1", "A", [1.0, 0.0]), ("m1", "B", [0.9, 0.1])])
        after = voices.enrol(before, "Tom", "m1", "A", [1.0, 0.0])
        self.assertEqual([e["voice"] for e in after["Aisha"]], ["B"])
        self.assertEqual([e["voice"] for e in after["Tom"]], ["A"])

    def test_a_lower_case_letter_is_the_same_slot(self):
        after = voices.enrol(roster(Aisha=[("m1", "A", [1.0, 0.0])]),
                             "Aisha", "m1", "a", [0.0, 1.0])
        self.assertEqual(len(after["Aisha"]), 1)

    def test_no_letter_changes_nothing(self):
        before = roster(Aisha=[("m1", "A", [1.0, 0.0])])
        self.assertEqual(voices.enrol(before, "Tom", "m2", "  ", [0.0, 1.0]), before)

    def test_only_the_newest_samples_are_kept(self):
        people = {}
        for day in range(1, 15):
            people = voices.enrol(people, "Aisha", f"2026-09-{day:02d}-1000-sync",
                                  "A", [float(day), 0.0])
        self.assertEqual(len(people["Aisha"]), voices.MAX_SAMPLES)
        self.assertEqual(people["Aisha"][0]["from"], "2026-09-05-1000-sync")

    def test_an_older_meeting_loses_to_a_full_roster(self):
        # A recording id starts with its date, so the newest ids sort last.
        people = {}
        for day in range(5, 16):
            people = voices.enrol(people, "Aisha", f"2026-09-{day:02d}-1000-sync",
                                  "A", [float(day), 0.0])
        after = voices.enrol(people, "Aisha", "2026-01-01-1000-sync", "A", [99.0, 0.0])
        self.assertNotIn("2026-01-01-1000-sync",
                         [entry["from"] for entry in after["Aisha"]])

    def test_enrolling_does_not_mutate_the_roster_it_was_given(self):
        before = roster(Aisha=[("m1", "A", [1.0, 0.0])])
        voices.enrol(before, "Tom", "m2", "A", [0.0, 1.0])
        self.assertEqual(before, roster(Aisha=[("m1", "A", [1.0, 0.0])]))


class Forgetting(unittest.TestCase):
    def test_a_person_can_be_dropped(self):
        after = voices.forget(roster(Aisha=[("m1", "A", [1.0, 0.0])],
                                     Marco=[("m2", "A", [0.0, 1.0])]), "Aisha")
        self.assertEqual(list(after), ["Marco"])

    def test_the_name_is_matched_ignoring_case(self):
        after = voices.forget(roster(Aisha=[("m1", "A", [1.0, 0.0])]), "AISHA")
        self.assertEqual(after, {})

    def test_an_unknown_name_changes_nothing(self):
        before = roster(Aisha=[("m1", "A", [1.0, 0.0])])
        self.assertEqual(voices.forget(before, "Nobody"), before)


class Wording(unittest.TestCase):
    """The list used to round 26 seconds down to "0 min", which read as junk."""

    def test_seconds_are_never_rounded_away(self):
        self.assertEqual(voices.spoken(26.3), "0m 26s")

    def test_minutes_and_seconds_both_show(self):
        self.assertEqual(voices.spoken(205.1), "3m 25s")

    def test_seconds_are_padded_so_the_column_lines_up(self):
        self.assertEqual(voices.spoken(121.0), "2m 01s")

    def test_nothing_said_is_still_readable(self):
        self.assertEqual(voices.spoken(0.0), "0m 00s")


class Arithmetic(unittest.TestCase):
    def test_the_mean_is_the_average_of_each_position(self):
        self.assertEqual(voices.mean([[0.0, 2.0], [1.0, 4.0]]), [0.5, 3.0])

    def test_an_identical_vector_scores_one(self):
        self.assertAlmostEqual(voices.similarity([3.0, 4.0], [3.0, 4.0]), 1.0)

    def test_length_does_not_matter_only_direction(self):
        self.assertAlmostEqual(voices.similarity([3.0, 4.0], [30.0, 40.0]), 1.0)

    def test_an_unrelated_vector_scores_zero(self):
        self.assertAlmostEqual(voices.similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_a_silent_vector_matches_nothing_instead_of_dividing_by_zero(self):
        self.assertEqual(voices.similarity([0.0, 0.0], [1.0, 0.0]), 0.0)

    def test_vectors_of_different_length_score_zero(self):
        self.assertEqual(voices.similarity([1.0, 0.0], [1.0, 0.0, 0.0]), 0.0)


class Matching(unittest.TestCase):
    def test_a_close_voice_is_named(self):
        found = voices.match(roster(Aisha=[("m1", "A", [1.0, 0.0])]), [0.99, 0.14], 0.9)
        self.assertEqual(found, "Aisha")

    def test_a_distant_voice_is_not_named(self):
        found = voices.match(roster(Aisha=[("m1", "A", [1.0, 0.0])]), [0.0, 1.0], 0.9)
        self.assertIsNone(found)

    def test_an_empty_roster_names_nobody(self):
        self.assertIsNone(voices.match({}, [1.0, 0.0], 0.5))

    def test_no_vector_names_nobody(self):
        self.assertIsNone(voices.match(roster(Aisha=[("m1", "A", [1.0, 0.0])]), [], 0.5))

    def test_the_closest_of_several_people_wins(self):
        people = roster(Aisha=[("m1", "A", [1.0, 0.0])], Marco=[("m2", "A", [0.0, 1.0])])
        self.assertEqual(voices.match(people, [0.9, 0.44], 0.5), "Aisha")
        self.assertEqual(voices.match(people, [0.44, 0.9], 0.5), "Marco")

    def test_a_second_sample_moves_the_stored_voice(self):
        # The claim the whole feature rests on: more samples shift a person's
        # voice towards the average of them, not towards the newest one.
        one = roster(Aisha=[("m1", "A", [1.0, 0.0])])
        two = voices.enrol(one, "Aisha", "m2", "A", [0.8, 0.6])
        query = [0.9, 0.44]
        self.assertGreater(best_score(two, query), best_score(one, query))
        # And it lands exactly on the mean, which is what sherpa's own manager
        # computes. Measured against SpeakerEmbeddingManager.score: identical.
        middle = [(1.0 + 0.8) / 2, (0.0 + 0.6) / 2]
        self.assertAlmostEqual(best_score(two, query), voices.similarity(query, middle))

    def test_scores_come_back_best_first(self):
        people = roster(Aisha=[("m1", "A", [1.0, 0.0])], Marco=[("m2", "A", [0.0, 1.0])])
        ranked = voices.scores(people, [0.9, 0.44])
        self.assertEqual([name for name, _ in ranked], ["Aisha", "Marco"])


class InAMeeting(unittest.TestCase):
    """The half that reads and writes the files beside one recording."""

    def setUp(self):
        self.notes = tempfile.mkdtemp()
        self.work = os.path.join(self.notes, ".recordings", "2026-09-01-1500-standup")
        os.makedirs(self.work)

    def prints(self, **letters):
        with open(os.path.join(self.work, "voiceprints.json"), "w", encoding="utf-8") as h:
            json.dump({letter: list(vector) for letter, vector in letters.items()}, h)

    def speakers(self, rows):
        with open(os.path.join(self.work, "speakers.json"), "w", encoding="utf-8") as h:
            json.dump(rows, h)

    def read(self, filename):
        return voices.read_letters(self.work, filename)

    def skip(self, text):
        with open(os.path.join(self.work, "skipped.txt"), "w", encoding="utf-8") as h:
            h.write(text)

    # -- matching --------------------------------------------------------

    def test_a_known_voice_is_written_to_matched(self):
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.prints(A=[0.99, 0.14])
        self.assertEqual(voices.match_recording(self.notes, self.work, 0.9),
                         {"A": "Aisha"})
        self.assertEqual(self.read("matched.txt"), {"A": "Aisha"})

    def test_an_unknown_voice_is_not_written(self):
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.prints(A=[0.0, 1.0])
        self.assertEqual(voices.match_recording(self.notes, self.work, 0.9), {})
        self.assertEqual(self.read("matched.txt"), {})

    def test_an_empty_roster_matches_nobody(self):
        self.prints(A=[1.0, 0.0])
        self.assertEqual(voices.match_recording(self.notes, self.work, 0.5), {})

    def test_a_forgotten_voice_stops_being_named(self):
        # `qn forget` then `qn redo` must not keep printing yesterday's answer.
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.prints(A=[1.0, 0.0])
        voices.match_recording(self.notes, self.work, 0.5)
        self.assertEqual(self.read("matched.txt"), {"A": "Aisha"})

        voices.save(self.notes, voices.forget(voices.load(self.notes), "Aisha"))
        self.assertEqual(voices.match_recording(self.notes, self.work, 0.5), {})
        self.assertEqual(self.read("matched.txt"), {})

    def test_a_voice_that_drifts_below_the_threshold_stops_being_named(self):
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.prints(A=[1.0, 0.0])
        voices.match_recording(self.notes, self.work, 0.5)
        self.assertEqual(voices.match_recording(self.notes, self.work, 1.5), {})
        self.assertEqual(self.read("matched.txt"), {})

    def test_no_voiceprints_leaves_nothing_behind(self):
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.assertEqual(voices.match_recording(self.notes, self.work, 0.5), {})
        self.assertEqual(self.read("matched.txt"), {})

    def test_a_letter_you_confirmed_is_left_alone(self):
        # Re-running the match after you corrected a name must not undo it.
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.prints(A=[1.0, 0.0], B=[1.0, 0.0])
        voices.write_letters(self.work, "confirmed.txt", {"A": "Tom"})
        self.assertEqual(voices.match_recording(self.notes, self.work, 0.5), {"B": "Aisha"})

    def test_a_letter_you_skipped_is_left_alone(self):
        # `qn skip` says this group is not one person. The next `qn redo` runs
        # the match again, and it must not put a name back on it.
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.prints(A=[1.0, 0.0], B=[1.0, 0.0])
        self.skip("A\n")
        self.assertEqual(voices.match_recording(self.notes, self.work, 0.5), {"B": "Aisha"})

    def test_a_skip_file_is_read_letter_by_letter(self):
        self.skip("a\n\nC\n")
        self.assertEqual(voices.read_skipped(self.work), {"A", "C"})

    def test_no_skip_file_skips_nothing(self):
        self.assertEqual(voices.read_skipped(self.work), set())

    # -- enrolling -------------------------------------------------------

    def test_confirming_a_voice_teaches_the_roster(self):
        self.prints(A=[1.0, 0.0])
        message = voices.enrol_from(self.notes, self.work, "A", "Aisha", "full")
        self.assertIn("learned Aisha", message)
        self.assertEqual(list(voices.load(self.notes)), ["Aisha"])

    def test_a_private_meeting_teaches_nothing(self):
        self.prints(A=[1.0, 0.0])
        message = voices.enrol_from(self.notes, self.work, "A", "Aisha", "local")
        self.assertIn("not learning", message)
        self.assertEqual(voices.load(self.notes), {})

    def test_a_meeting_without_diarization_teaches_nothing(self):
        message = voices.enrol_from(self.notes, self.work, "A", "Aisha", "full")
        self.assertIn("not grouped by voice", message)
        self.assertEqual(voices.load(self.notes), {})

    def test_a_letter_that_was_never_heard_teaches_nothing(self):
        self.prints(A=[1.0, 0.0])
        message = voices.enrol_from(self.notes, self.work, "C", "Aisha", "full")
        self.assertIn("no voice C here", message)

    def test_the_entry_is_filed_under_the_meeting_id(self):
        self.prints(A=[1.0, 0.0])
        voices.enrol_from(self.notes, self.work, "A", "Aisha", "full")
        stored = voices.load(self.notes)["Aisha"]
        self.assertEqual(stored[0]["from"], "2026-09-01-1500-standup")

    def test_a_lower_case_letter_still_finds_the_voice(self):
        self.prints(A=[1.0, 0.0])
        self.assertIn("learned", voices.enrol_from(self.notes, self.work, "a", "Aisha", "full"))

    # -- the waiting list ------------------------------------------------

    def test_an_unnamed_voice_is_waiting(self):
        self.prints(A=[1.0, 0.0], B=[0.0, 1.0])
        self.speakers([{"start_ms": 0, "end_ms": 120_000, "speaker": "A"},
                       {"start_ms": 0, "end_ms": 90_000, "speaker": "B"}])
        waiting = voices.pending(self.notes)
        self.assertEqual([(letter, int(seconds)) for _, letter, seconds in waiting],
                         [("A", 120), ("B", 90)])

    def test_a_confirmed_voice_is_not_waiting(self):
        self.prints(A=[1.0, 0.0])
        voices.write_letters(self.work, "confirmed.txt", {"A": "Aisha"})
        self.assertEqual(voices.pending(self.notes), [])

    def test_a_matched_voice_is_not_waiting(self):
        self.prints(A=[1.0, 0.0])
        voices.write_letters(self.work, "matched.txt", {"A": "Aisha"})
        self.assertEqual(voices.pending(self.notes), [])

    def test_a_skipped_voice_is_not_waiting(self):
        self.prints(A=[1.0, 0.0])
        self.speakers([{"start_ms": 0, "end_ms": 120_000, "speaker": "A"}])
        self.skip("A\n")
        self.assertEqual(voices.pending(self.notes), [])

    def test_skipping_one_voice_leaves_the_others_waiting(self):
        self.prints(A=[1.0, 0.0], B=[0.0, 1.0])
        self.speakers([{"start_ms": 0, "end_ms": 120_000, "speaker": "A"},
                       {"start_ms": 0, "end_ms": 90_000, "speaker": "B"}])
        self.skip("A\n")
        self.assertEqual([letter for _, letter, _ in voices.pending(self.notes)], ["B"])

    def test_no_recordings_folder_is_not_an_error(self):
        self.assertEqual(voices.pending(tempfile.mkdtemp()), [])

    def test_talking_time_adds_up_across_segments(self):
        self.speakers([{"start_ms": 0, "end_ms": 30_000, "speaker": "A"},
                       {"start_ms": 60_000, "end_ms": 90_000, "speaker": "A"}])
        self.assertEqual(voices.talk_seconds(self.work), {"A": 60.0})

    def test_a_short_voice_is_refused_with_its_real_number(self):
        # The defect: "0 min of talking" invited the reader to name a scrap,
        # and naming it made recognition worse. Now it says 12s, and refuses.
        self.prints(B=[0.0, 1.0])
        self.speakers([{"start_ms": 0, "end_ms": 12_000, "speaker": "A"}])
        message = voices.enrol_from(self.notes, self.work, "A", "Ravi", "full")
        self.assertIn("0m 12s", message)
        self.assertIn("too little to remember", message)
        self.assertEqual(voices.load(self.notes), {})

    def test_a_short_voice_is_refused_even_when_a_print_exists(self):
        # Files written before the floor existed still hold these prints.
        self.prints(A=[1.0, 0.0])
        self.speakers([{"start_ms": 0, "end_ms": 12_000, "speaker": "A"}])
        message = voices.enrol_from(self.notes, self.work, "A", "Ravi", "full")
        self.assertIn("too little to remember", message)
        self.assertEqual(voices.load(self.notes), {})

    def test_a_voice_that_earns_a_letter_is_worth_remembering(self):
        # The floor for a print is the floor for a letter. A separate, higher
        # one of 60s refused people the answer keys show were never a problem.
        self.prints(A=[1.0, 0.0])
        self.speakers([{"start_ms": 0, "end_ms": 26_300, "speaker": "A"}])
        message = voices.enrol_from(self.notes, self.work, "A", "Ravi", "full")
        self.assertIn("learned", message)
        self.assertIn("Ravi", voices.load(self.notes))

    def test_a_dropped_sample_never_reports_success(self):
        # The cap keeps the newest. Naming an older meeting when the roster is
        # full stores nothing, and "learned" would be a lie.
        people = {}
        for day in range(5, 16):
            people = voices.enrol(people, "Ravi", f"2026-09-{day:02d}-1000-sync",
                                  "A", [float(day), 0.0])
        voices.save(self.notes, people)
        self.prints(A=[1.0, 0.0])
        self.speakers([{"start_ms": 0, "end_ms": 120_000, "speaker": "A"}])
        message = voices.enrol_from(self.notes, self.work, "A", "Ravi", "full")
        self.assertIn("newer samples", message)
        self.assertIn("roster is unchanged", message)

    def test_a_short_voice_never_reaches_the_waiting_list(self):
        self.prints(A=[1.0, 0.0], B=[0.0, 1.0])
        self.speakers([{"start_ms": 0, "end_ms": 12_000, "speaker": "A"},
                       {"start_ms": 0, "end_ms": 120_000, "speaker": "B"}])
        self.assertEqual([letter for _, letter, _ in voices.pending(self.notes)], ["B"])

    def test_the_waiting_list_puts_the_best_sample_first(self):
        self.prints(A=[1.0, 0.0], B=[0.0, 1.0], C=[0.5, 0.5])
        self.speakers([{"start_ms": 0, "end_ms": 120_000, "speaker": "A"},
                       {"start_ms": 0, "end_ms": 300_000, "speaker": "B"},
                       {"start_ms": 0, "end_ms": 61_000, "speaker": "C"}])
        self.assertEqual([letter for _, letter, _ in voices.pending(self.notes)],
                         ["B", "A", "C"])

    # -- the screen ------------------------------------------------------

    def test_an_empty_roster_says_so_instead_of_printing_nothing(self):
        screen = voices.render(self.notes)
        self.assertIn("nobody yet", screen)

    def test_the_screen_names_who_is_known_and_who_is_waiting(self):
        voices.save(self.notes, roster(Aisha=[("m0", "A", [1.0, 0.0])]))
        self.prints(B=[0.0, 1.0])
        self.speakers([{"start_ms": 0, "end_ms": 120_000, "speaker": "B"}])
        screen = voices.render(self.notes)
        self.assertIn("Aisha", screen)
        self.assertIn("2026-09-01-1500-standup", screen)




if __name__ == "__main__":
    unittest.main(verbosity=2)
