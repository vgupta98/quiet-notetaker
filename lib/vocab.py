"""Learn the words your meetings actually use, and tell whisper about them.

Whisper guesses at words it has never been told about. "On-call" came out as
"uncle" in every note until Claude fixed it afterwards. Priming the decoder
fixes it at the source instead.

The danger is a feedback loop: harvest whisper's own mistakes, feed them back,
and the mistakes get reinforced until they are all you see. So this module
never learns from what whisper heard.

Two sources only:

  trusted     attendee names, which were typed by a person or read from a
              calendar. Never produced by the decoder, so admitted on sight.

  corrected   terms that appear in the NOTES but not in that meeting's
              TRANSCRIPT. By construction those are words Claude put right,
              which is the opposite of whisper's own output. They still need
              to show up in two different meetings before being believed.

Anything whisper actually produced is disqualified, permanently. That is what
breaks the loop.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))

import index  # noqa: E402  (path set above)

import names

VOCAB_FILE = "vocabulary.txt"
REMOVED_FILE = ".vocabulary-removed"

# whisper's initial prompt is capped at n_text_ctx/2, about 224 tokens for the
# small model. Terms cost roughly three tokens each with their separator.
MAX_TERMS = 64

# A heard term must turn up in this many different meetings before it counts.
# One bad meeting must never be able to promote its own mistake.
MIN_MEETINGS = 2

WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*")

# The note's own furniture. These appear in every note ever written, so they
# would otherwise look like the most confirmed vocabulary in the corpus.
SCAFFOLD = frozenset(
    """summary decisions transcript questions action items none their
    jan feb mar apr may jun jul aug sep oct nov dec
    january february march april june july august september october
    november december monday tuesday wednesday thursday friday saturday
    sunday""".split()
)

# Capitalised words that only ever start a sentence.
SENTENCE_STARTERS = frozenset(
    """a an and are as at be but by for from he her his if in is it its of on or she
    that the their them then there they this to was we were what when where which who
    will with you your i our us all also any can could do does did had has have how
    into just like may more most no not now one only other out over said same should
    so some such than these those through time two up use very way well would""".split()
)


def is_interesting(word: str) -> bool:
    """True for the shapes that domain terms take, and nothing else.

    Proper nouns, hyphenated technical terms, and camel case. Ordinary English
    is rejected, because whisper already spells it correctly and every slot in
    the prompt is worth more to a word it would otherwise guess at.
    """
    if len(word) < 3:
        return False
    folded = word.lower()
    if folded in SENTENCE_STARTERS or folded in SCAFFOLD:
        return False
    if "-" in word:
        return True
    if any(character.isupper() for character in word[1:]):
        return True
    return word[0].isupper()


def strip_scaffolding(notes_text: str) -> str:
    """Drop the parts of a note that the template wrote, not the meeting.

    Headings and the italic date/attendee line are identical in every note, so
    harvesting them would promote the template's own words above real content.
    """
    kept = []
    for line in notes_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("_") and stripped.endswith("_"):
            continue
        kept.append(line)
    return "\n".join(kept)


BULLET = re.compile(r"^\s*(?:[-*+]\s*(?:\[[ xX]\]\s*)?|\d+[.)]\s*)")
# "Priya: chase the ticket" — the template puts the owner's name here, so the
# colon marks a person rather than an accident of capitalisation.
OWNER_PREFIX = re.compile(r"^([A-Z][A-Za-z'-]{2,})\s*:\s+")
# A colon starts a clause too: "Priya: Share the doc" capitalises by
# position, exactly like a sentence would.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?:])\s+")


def _stands_on_its_own(word: str) -> bool:
    """True when a word looks like a name regardless of where it sits.

    A capital at the start of a bullet or a sentence proves nothing — "Ship the
    release" is not about a vessel. Only an inner capital or a hyphen survives
    the position test.
    """
    return "-" in word or any(character.isupper() for character in word[1:])


def terms_in(text: str) -> set[str]:
    """Every interesting term in a block of text, as written.

    Position matters: the first word of a bullet or sentence is capitalised by
    grammar, not by being a proper noun, so it needs a second signal.
    """
    found: set[str] = set()
    for line in text.splitlines():
        body = BULLET.sub("", line.strip())
        owner = OWNER_PREFIX.match(body)
        if owner is not None:
            if is_interesting(owner.group(1)):
                found.add(owner.group(1))
            body = body[owner.end():]
        for sentence in SENTENCE_SPLIT.split(body):
            for position, match in enumerate(WORD.finditer(sentence)):
                word = match.group(0)
                if not is_interesting(word):
                    continue
                if position == 0 and not _stands_on_its_own(word):
                    continue
                found.add(word)
    return found


def _folded(words) -> set[str]:
    return {word.lower() for word in words}


def harvest(notes: list[dict]) -> list[str]:
    """Choose the vocabulary from a set of parsed notes.

    `notes` are dicts as produced by index.parse_note. Private meetings are
    already absent, because parse_note refuses to return them.
    """
    trusted: dict[str, str] = {}
    spread: dict[str, set[str]] = {}
    casing: dict[str, str] = {}
    # Any spelling whisper produced anywhere is disqualified everywhere. A term
    # the decoder can already reach does not need a slot, and if the spelling
    # is wrong we must not entrench it.
    heard: set[str] = set()

    for note in notes:
        for person in note.get("attendees", []):
            # An address gives up its local part and its company, never its
            # top-level domain. "com" primes the decoder for nothing and costs
            # one of the slots a real term needs.
            for word in WORD.findall(names.vocabulary_source(person)):
                if len(word) >= 3:
                    trusted.setdefault(word.lower(), word)

        transcript_terms = _folded(terms_in(note.get("transcript_text", "")))
        heard |= transcript_terms

        for term in terms_in(strip_scaffolding(note.get("notes_text", ""))):
            folded = term.lower()
            if folded in transcript_terms:
                continue
            spread.setdefault(folded, set()).add(note["id"])
            casing.setdefault(folded, term)

    corrected = [
        folded
        for folded, meetings in spread.items()
        if len(meetings) >= MIN_MEETINGS and folded not in heard and folded not in trusted
    ]
    corrected.sort(key=lambda folded: (-len(spread[folded]), folded))

    chosen = [trusted[folded] for folded in sorted(trusted)]
    chosen += [casing[folded] for folded in corrected]
    return chosen[:MAX_TERMS]


def load_removed(directory: str) -> set[str]:
    """Terms the user deleted. They never come back."""
    path = os.path.join(directory, REMOVED_FILE)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip().lower() for line in handle if line.strip()}


def merge_with_existing(existing: list[str], harvested: list[str], removed: set[str]) -> list[str]:
    """Keep what the user has, add what is new, honour what they deleted.

    The removal list only ever silences our own suggestions. A word the user
    typed in themselves always wins, even one they deleted earlier — otherwise
    the file quietly reverts their edit and the README's "add your own freely"
    is a lie.
    """
    out: list[str] = []
    seen: set[str] = set()
    for term in existing:
        folded = term.lower()
        if folded not in seen:
            seen.add(folded)
            out.append(term)
    for term in harvested:
        folded = term.lower()
        if folded in seen or folded in removed:
            continue
        seen.add(folded)
        out.append(term)
    return out[:MAX_TERMS]


def read_vocabulary(directory: str) -> list[str]:
    """The active list, as the user last left it."""
    path = os.path.join(directory, VOCAB_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.startswith("#")]


def refresh(directory: str) -> list[str]:
    """Re-harvest, merge, and record anything the user deleted since last time."""
    notes = []
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if name.endswith(".md"):
                note = index.parse_note(os.path.join(directory, name))
                if note is not None:
                    notes.append(note)

    before = read_vocabulary(directory)
    harvested = harvest(notes)
    removed = load_removed(directory)

    # A term we suggested before, that is no longer in the file, was deleted on
    # purpose. Remember that so it is never suggested again.
    deleted = (_folded(_previous_suggestions(directory)) - _folded(before)) - removed
    if deleted:
        removed |= deleted
        with open(os.path.join(directory, REMOVED_FILE), "a", encoding="utf-8") as handle:
            for term in sorted(deleted):
                handle.write(term + "\n")

    merged = merge_with_existing(before, harvested, removed)
    _write(directory, merged)
    # Remember only what WE proposed. Recording the merged list turned the
    # user's own additions into suggestions, so deleting one tombstoned it.
    _remember_suggestions(directory, harvested)
    return merged


def _suggestions_path(directory: str) -> str:
    return os.path.join(directory, ".vocabulary-suggested")


def _previous_suggestions(directory: str) -> list[str]:
    path = _suggestions_path(directory)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def _remember_suggestions(directory: str, terms: list[str]) -> None:
    with open(_suggestions_path(directory), "w", encoding="utf-8") as handle:
        handle.write("\n".join(terms) + ("\n" if terms else ""))


def _write(directory: str, terms: list[str]) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, VOCAB_FILE), "w", encoding="utf-8") as handle:
        handle.write("# Words whisper should expect in your meetings.\n")
        handle.write("# Delete a line to remove it for good. Add your own freely.\n")
        for term in terms:
            handle.write(term + "\n")


def as_prompt(terms: list[str]) -> str:
    """The string handed to whisper's --prompt."""
    return ", ".join(terms)


if __name__ == "__main__":
    where = sys.argv[1] if len(sys.argv) > 1 else index.notes_dir()
    if "--prompt" in sys.argv:
        print(as_prompt(read_vocabulary(where)))
    else:
        chosen = refresh(where)
        print(f"{len(chosen)} terms in {os.path.join(where, VOCAB_FILE)}")
