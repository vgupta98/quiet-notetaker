#!/usr/bin/env python3
"""Write a deterministic synthetic meeting corpus that matches SPEC.md.

    python3 test/fixtures.py <output-dir> [--count N]

The same arguments always produce byte-identical files. Nothing reads the
clock and nothing uses the ``random`` module. Import ``build_corpus`` to get
the ground truth back as data:

    from fixtures import build_corpus
    meetings = build_corpus("/tmp/notes", count=10)

Stdlib only. No third-party imports.
"""

import argparse
import json
import pathlib
import shutil
import sys

# ---------------------------------------------------------------------------
# Search phrases. Other tests import these instead of hardcoding strings.
# ---------------------------------------------------------------------------

#: Appears in the Summary section of NOTES_ONLY_ID. It is in no transcript
#: anywhere in the corpus, so a search that finds it must report
#: matched_in == "notes".
NOTES_ONLY_PHRASE = "quokka provisioning"

#: Appears in the transcript of TRANSCRIPT_ONLY_ID, inside the fenced block
#: of the note and in transcript.txt. It is in no AI section anywhere in the
#: corpus, so a search that finds it must report matched_in == "transcript".
TRANSCRIPT_ONLY_PHRASE = "zeppelin calibration"

#: Meeting ids that carry the phrases above.
NOTES_ONLY_ID = "2026-05-21-1630-design-critique"
TRANSCRIPT_ONLY_ID = "2026-05-06-1105-sdk-sync"

#: Meeting with capture: warn and a non-empty warnings list.
WARN_ID = "2026-06-02-0815-standup"

#: Meeting whose transcript is longer than 100 lines.
LONG_TRANSCRIPT_ID = "2026-06-18-1245-long-retro"

#: Meetings held back from the index (sharing: local, consent: local).
LOCAL_IDS = ["2026-04-15-1400-vendor-call", "2026-07-30-1015-hiring-debrief"]

#: The five headings every ``sharing: full`` note carries, in order.
SECTION_NAMES = [
    "Summary",
    "Decisions",
    "My action items",
    "Their action items",
    "Open questions",
]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DOT = "·"

# ---------------------------------------------------------------------------
# Text pools. Index arithmetic picks from these, so output never varies.
# ---------------------------------------------------------------------------

SUMMARY_POOL = [
    "Walked through the open items from last week and closed most of them.",
    "Agreed the release train slips by one week to absorb the migration.",
    "Reviewed the error budget and found the mobile client burns most of it.",
    "Talked through the onboarding drop-off and where people give up.",
    "Compared two storage layouts and picked the one with fewer writes.",
    "Went over the support queue and grouped the tickets by root cause.",
    "Sketched the rollout order for the three regions.",
    "Checked the load test numbers against the target and found a gap.",
]

DECISION_POOL = [
    "Ship behind a flag and turn it on for internal users first.",
    "Keep the old endpoint alive for two more releases.",
    "Move the batch job off the shared worker pool.",
    "Drop the second retry and let the client back off instead.",
    "Write the migration as one script, not three.",
    "Freeze the schema until the audit is done.",
]

MY_ACTION_POOL = [
    "Write the migration script and post it for review by Friday",
    "Pull the last thirty days of latency numbers into a chart",
    "Draft the rollout plan for the three regions",
    "Reply to the support thread with the workaround",
    "Cut a patch release once the flag lands",
    "Book a follow-up with the platform team",
]

THEIR_ACTION_POOL = [
    "Confirm the storage quota with the infrastructure team",
    "Send the failing request ids from the last outage",
    "Update the runbook with the new alert thresholds",
    "Review the schema change before the freeze",
    "Share the customer list for the first region",
    "Check whether the old endpoint still has traffic",
]

QUESTION_POOL = [
    "Who owns the alert once the batch job moves?",
    "Does the old client handle an empty payload?",
    "How long can the freeze last before it blocks the audit?",
    "Is the quota per region or per account?",
    "What happens to in-flight jobs during the cutover?",
    "Which team pages when the flag is on?",
]

TALK_POOL = [
    "okay so where did we land on the migration",
    "we ran it against a copy of production last night",
    "and it took about forty minutes end to end",
    "that is longer than the window we agreed",
    "right but most of that is the index rebuild",
    "can we do the index rebuild afterwards",
    "probably yes if we accept slower reads for a while",
    "how much slower are we talking",
    "maybe twice the latency for the first hour",
    "that is fine for internal users",
    "let us keep it behind the flag then",
    "i will write it up and send it round",
    "one more thing about the support queue",
    "we had eleven tickets and nine were the same bug",
    "the fix is already merged it just needs a release",
    "i can cut that tomorrow morning",
]


def _pick(pool, index):
    return pool[index % len(pool)]


def _take(pool, index, count):
    return [pool[(index + step) % len(pool)] for step in range(count)]


# ---------------------------------------------------------------------------
# The corpus. Fixed, ordered, and hand-picked so every test case is covered.
# ---------------------------------------------------------------------------

SPECS = [
    {
        "id": "2026-03-04-0930-roadmap-review",
        "title": "Roadmap Review",
        "attendees": ["Priya", "Arjun"],
        "sharing": "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": True,
        "my_done": [False, True],
        "their_done": [False, False],
        "lines": 18,
        "step": 27,
    },
    {
        "id": "2026-04-15-1400-vendor-call",
        "title": "Vendor Call",
        "attendees": ["Dana"],
        "sharing": "local",
        "capture": "ok",
        "warnings": [],
        "their_owner": True,
        "my_done": [],
        "their_done": [],
        "lines": 14,
        "step": 31,
    },
    {
        "id": "2026-05-06-1105-sdk-sync",
        "title": "SDK Sync",
        "attendees": [],
        "sharing": "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": False,
        "my_done": [False, False],
        "their_done": [True, False],
        "lines": 16,
        "step": 23,
    },
    {
        "id": "2026-05-21-1630-design-critique",
        "title": "Design Critique",
        "attendees": ["Priya", "Kenji", "Arjun"],
        "sharing": "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": True,
        "my_done": [True, True],
        "their_done": [False, True],
        "lines": 20,
        "step": 19,
    },
    {
        "id": WARN_ID,
        "title": "Standup",
        "attendees": ["Arjun"],
        "sharing": "full",
        "capture": "warn",
        "warnings": [
            "microphone level was very low for most of the recording",
            "system audio dropped for 12 seconds",
        ],
        "their_owner": True,
        "my_done": [False],
        "their_done": [False],
        "lines": 10,
        "step": 17,
    },
    {
        "id": LONG_TRANSCRIPT_ID,
        "title": "Long Retro",
        "attendees": ["Priya", "Kenji", "Dana", "Arjun"],
        "sharing": "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": True,
        "my_done": [False, True],
        "their_done": [False, True],
        "lines": 124,
        "step": 55,
    },
    {
        "id": "2026-07-09-1700-budget-planning",
        "title": "Budget Planning",
        "attendees": ["Mira", "Dana"],
        "sharing": "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": False,
        "my_done": [True, True],
        "their_done": [True, True],
        "lines": 22,
        "step": 29,
    },
    {
        "id": "2026-07-30-1015-hiring-debrief",
        "title": "Hiring Debrief",
        "attendees": ["Mira"],
        "sharing": "local",
        "capture": "ok",
        "warnings": [],
        "their_owner": True,
        "my_done": [],
        "their_done": [],
        "lines": 12,
        "step": 37,
    },
    {
        "id": "2026-08-11-0900-incident-postmortem",
        "title": "Incident Postmortem",
        "attendees": ["Kenji", "Dana"],
        "sharing": "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": True,
        "my_done": [False, True],
        "their_done": [True, False],
        "lines": 26,
        "step": 21,
    },
    {
        "id": "2026-08-20-1535-partner-intro",
        "title": "Partner Intro",
        "attendees": [],
        "sharing": "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": False,
        "my_done": [False, False],
        "their_done": [False, False],
        "lines": 15,
        "step": 33,
    },
]

#: Number of hand-written meetings. Ask for at least this many to get every
#: documented edge case.
CORE_COUNT = len(SPECS)


def _extra_spec(index):
    """Build meeting number ``index`` (0-based) beyond the hand-written set."""
    day = 1 + (index * 3) % 27
    hour = 9 + index % 8
    number = index + 1
    return {
        "id": "2026-09-%02d-%02d00-extra-sync-%d" % (day, hour, number),
        "title": "Extra Sync %d" % number,
        "attendees": ["Priya", "Arjun"] if index % 2 == 0 else [],
        "sharing": "local" if index % 5 == 4 else "full",
        "capture": "ok",
        "warnings": [],
        "their_owner": index % 2 == 0,
        "my_done": [] if index % 5 == 4 else [index % 2 == 0, index % 3 == 0],
        "their_done": [] if index % 5 == 4 else [index % 3 == 1, False],
        "lines": 12 + index % 7,
        "step": 20 + index % 11,
    }


def specs_for(count):
    """Return ``count`` meeting specs, deterministically."""
    if count <= CORE_COUNT:
        return [dict(spec) for spec in SPECS[:count]]
    extra = [_extra_spec(i) for i in range(count - CORE_COUNT)]
    return [dict(spec) for spec in SPECS] + extra


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pretty_date(date):
    """2026-03-04 09:30 -> 04 Mar 2026, 09:30 (the format qn writes)."""
    day, month, rest = date[8:10], int(date[5:7]), date[11:]
    return "%s %s %s, %s" % (day, MONTHS[month - 1], date[0:4], rest)


def _date_of(meeting_id):
    """2026-03-04-0930-x -> 2026-03-04 09:30."""
    parts = meeting_id.split("-")
    return "%s-%s-%s %s:%s" % (parts[0], parts[1], parts[2],
                               parts[3][:2], parts[3][2:])


def _yaml_list(values):
    if not values:
        return "[]"
    return "[%s]" % ", ".join(values)


def _yaml_quoted_list(values):
    if not values:
        return "[]"
    return "[%s]" % ", ".join('"%s"' % value for value in values)


def _clock(seconds):
    return "[%02d:%02d]" % (seconds // 60, seconds % 60)


def _transcript(spec, index):
    """Alternating Them/Me lines in the SPEC transcript format."""
    lines = []
    segments = []
    at = 3
    for line_no in range(spec["lines"]):
        speaker = "Them" if line_no % 2 == 0 else "Me"
        text = _pick(TALK_POOL, index * 3 + line_no)
        if spec["id"] == TRANSCRIPT_ONLY_ID and line_no == 5:
            text = "the %s run finished overnight" % TRANSCRIPT_ONLY_PHRASE
        lines.append("%s %s: %s" % (_clock(at), speaker, text))
        segments.append((at * 1000, (at + spec["step"] - 2) * 1000,
                         speaker, text))
        at += spec["step"]
    return lines, segments


def _sections(spec, index):
    """The five AI sections, as a name -> list-of-lines mapping."""
    summary = ["- " + line for line in _take(SUMMARY_POOL, index, 3)]
    if spec["id"] == NOTES_ONLY_ID:
        summary[1] = "- The %s work is parked until the audit closes." % (
            NOTES_ONLY_PHRASE)

    decisions = ["- " + line for line in _take(DECISION_POOL, index, 2)]

    mine = []
    for slot, done in enumerate(spec["my_done"]):
        text = _pick(MY_ACTION_POOL, index + slot)
        mine.append("- [%s] %s" % ("x" if done else " ", text))

    theirs = []
    for slot, done in enumerate(spec["their_done"]):
        text = _pick(THEIR_ACTION_POOL, index + slot)
        if spec["their_owner"] and spec["attendees"]:
            owner = spec["attendees"][slot % len(spec["attendees"])]
            text = "%s: %s" % (owner, text)
        theirs.append("- [%s] %s" % ("x" if done else " ", text))

    questions = ["- " + line for line in _take(QUESTION_POOL, index, 2)]

    return {
        "Summary": summary,
        "Decisions": decisions,
        "My action items": mine or ["- None"],
        "Their action items": theirs or ["- None"],
        "Open questions": questions,
    }


def _actions(spec, sections):
    """Ground truth for meetings_actions, derived from the rendered lines."""
    items = []
    for whose, heading in (("mine", "My action items"),
                           ("theirs", "Their action items")):
        for line in sections[heading]:
            if not line.startswith("- ["):
                continue
            done = line[3] == "x"
            text = line[6:]
            owner = None
            if whose == "theirs" and ": " in text:
                head, tail = text.split(": ", 1)
                if head in spec["attendees"]:
                    owner, text = head, tail
            items.append({"whose": whose, "owner": owner,
                          "text": text, "done": done})
    return items


def _summary_body(sections):
    """The part Claude writes: five headings, nothing else."""
    blocks = []
    for name in SECTION_NAMES:
        blocks.append("## %s\n%s\n" % (name, "\n".join(sections[name])))
    return "\n".join(blocks)


def _note_text(spec, date, sections, transcript_lines):
    people = ", ".join(spec["attendees"])
    head = [
        "---",
        "title: %s" % spec["title"],
        "date: %s" % date,
        "attendees: %s" % _yaml_list(spec["attendees"]),
        "sharing: %s" % spec["sharing"],
        "capture: %s" % spec["capture"],
        "warnings: %s" % _yaml_quoted_list(spec["warnings"]),
        "---",
        "",
        "# %s" % spec["title"],
        "",
    ]
    byline = "_%s_" % _pretty_date(date)
    if people:
        byline = "_%s_ %s _With: %s_" % (_pretty_date(date), DOT, people)
    head.append(byline)
    head.append("")

    if spec["sharing"] == "full":
        head.append(_summary_body(sections))
    tail = [
        "---",
        "",
        "## Transcript",
        "",
        "```",
        "\n".join(transcript_lines),
        "```",
        "",
    ]
    return "\n".join(head + tail)


def _whisper_json(segments, speaker):
    mine = [seg for seg in segments if seg[2] == speaker]
    return {
        "transcription": [
            {
                "timestamps": {"from": "00:00:00,000", "to": "00:00:00,000"},
                "offsets": {"from": start, "to": end},
                "text": " " + text,
            }
            for start, end, _, text in mine
        ]
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _guard(root):
    home = pathlib.Path.home().resolve()
    if root == home or root == home / "Meetings":
        raise SystemExit(
            "fixtures.py: refusing to write the corpus to %s" % root)


def build_corpus(path, count=CORE_COUNT):
    """Write the corpus under ``path`` and return what it wrote.

    Each returned dict holds the ground truth for one meeting: id, title,
    date, attendees, sharing, capture, warnings, consent, paths, the parsed
    sections, the action items, and the transcript lines.
    """
    root = pathlib.Path(path).expanduser().resolve()
    _guard(root)

    recordings = root / ".recordings"
    if root.exists():
        for stale in sorted(root.glob("*.md")):
            stale.unlink()
        if recordings.exists():
            shutil.rmtree(recordings)
        index = root / ".index.db"
        if index.exists():
            index.unlink()
    root.mkdir(parents=True, exist_ok=True)
    recordings.mkdir(parents=True, exist_ok=True)

    built = []
    for index, spec in enumerate(specs_for(count)):
        date = _date_of(spec["id"])
        transcript_lines, segments = _transcript(spec, index)
        sections = _sections(spec, index)

        work = recordings / spec["id"]
        work.mkdir(parents=True, exist_ok=True)

        note = root / ("%s.md" % spec["id"])
        note.write_text(_note_text(spec, date, sections, transcript_lines),
                        encoding="utf-8")

        transcript = work / "transcript.txt"
        transcript.write_text("\n".join(transcript_lines) + "\n",
                              encoding="utf-8")

        for track, speaker in (("them", "Them"), ("me", "Me")):
            (work / ("%s.json" % track)).write_text(
                json.dumps(_whisper_json(segments, speaker),
                           indent=2, sort_keys=True) + "\n",
                encoding="utf-8")

        consent = spec["sharing"]
        (work / "consent").write_text(consent + "\n", encoding="utf-8")

        if spec["attendees"]:
            (work / "attendees.txt").write_text(
                ", ".join(spec["attendees"]) + "\n", encoding="utf-8")

        if spec["sharing"] == "full":
            (work / "summary.md").write_text(
                _summary_body(sections), encoding="utf-8")

        built.append({
            "id": spec["id"],
            "title": spec["title"],
            "date": date,
            "attendees": list(spec["attendees"]),
            "sharing": spec["sharing"],
            "capture": spec["capture"],
            "warnings": list(spec["warnings"]),
            "consent": consent,
            "note_path": str(note),
            "recording_dir": str(work),
            "sections": sections if spec["sharing"] == "full" else {},
            "actions": _actions(spec, sections) if spec["sharing"] == "full" else [],
            "transcript_lines": transcript_lines,
        })
    return built


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write a deterministic synthetic meeting corpus.")
    parser.add_argument("output_dir")
    parser.add_argument("--count", type=int, default=CORE_COUNT,
                        help="how many meetings to write (default %d)"
                             % CORE_COUNT)
    args = parser.parse_args(argv)
    if args.count < 1:
        raise SystemExit("fixtures.py: --count must be 1 or more")

    built = build_corpus(args.output_dir, args.count)
    print("wrote %d meetings to %s" % (len(built), args.output_dir))
    for meeting in built:
        print("  %s  %s  %s" % (meeting["id"], meeting["sharing"],
                                meeting["capture"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
