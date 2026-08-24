#!/usr/bin/env python3
"""Read a person out of whatever a calendar invite called them.

An attendee arrives as a name someone typed, or as an email address. Both
`people.py` and `vocab.py` have to make sense of the same string, and they need
different things from it:

    aisha@example.com  →  a name for the roster:  "Aisha"
                             →  words for whisper:      "aisha", "example"

Getting that wrong is not cosmetic. The roster listed the raw address, which
Claude cannot match to "Aisha" in a transcript, and the vocabulary learned
"com" — a term that primes the decoder for nothing and costs one of its 64
slots. Every new colleague's invite repeated both.

One module, because it is one rule. Two near-copies would drift.
"""

from __future__ import annotations

import re

# A local part, an @, and a domain with at least one dot and a letters-only
# last label. Anything else is treated as a name someone typed.
EMAIL = re.compile(r"^(?P<local>[^@\s]+)@(?P<domain>[^@\s]+\.[A-Za-z]{2,})$")

# The characters that separate words inside a local part.
LOCAL_SPLIT = re.compile(r"[._-]+")


def looks_like_email(raw: str) -> bool:
    return EMAIL.match(str(raw).strip()) is not None


def display_name(raw: str) -> str:
    """The name to show a person, from a name or from an address.

    A string that is not an address comes back untouched. Names people type
    are already the way they want them.
    """
    text = str(raw).strip()
    match = EMAIL.match(text)
    if match is None:
        return text

    # `sam+notes@` is Sam. The tag is routing, not part of the name.
    local = match.group("local").split("+", 1)[0]

    parts = [part for part in LOCAL_SPLIT.split(local) if part and not part.isdigit()]
    # Only capitalise a part that is entirely lower case. `.capitalize()` on
    # McDonald gives Mcdonald, which is a worse answer than doing nothing.
    parts = [part.capitalize() if part.islower() else part for part in parts]

    return " ".join(parts) or text


def vocabulary_source(raw: str) -> str:
    """The part of an attendee that is worth teaching whisper.

    The last domain label is a top-level domain. Nobody says "com" out loud,
    and `vocab.py` drops anything shorter than three characters, so dropping
    just the last label is enough for `co.uk` too.
    """
    text = str(raw).strip()
    match = EMAIL.match(text)
    if match is None:
        return text

    labels = match.group("domain").split(".")
    return " ".join([match.group("local")] + labels[:-1])
