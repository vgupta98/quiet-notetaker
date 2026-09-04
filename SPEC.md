# Contract

Everything in this repo agrees on the shapes below. Change them here first.

## Layout

```
~/Meetings/
  2026-08-20-1535-sdk-sync.md          the note. source of truth.
  .recordings/2026-08-20-1535-sdk-sync/
      them.m4a  me.m4a  them.json  me.json  transcript.txt  summary.md
      consent                              one word: full | local | none
      consent.answered                     present once a human has answered
  .index.db                                disposable. rebuilt by rescanning.
```

**Meeting id** = the folder/file stem, e.g. `2026-08-20-1535-sdk-sync`.
Format: `YYYY-MM-DD-HHMM-<slug>`. It is the only identifier. No UUIDs.

## Note file

```markdown
---
title: SDK Sync
date: 2026-08-20 15:35
attendees: [Priya, Arjun]
sharing: full
capture: ok
warnings: []
---

# SDK Sync

_20 Aug 2026, 15:35_ · _With: Priya, Arjun_

## Summary
- ...

## Decisions
- ...

## My action items
- [ ] ...

## Their action items
- [ ] Priya: ...

## Open questions
- ...

---

## Transcript

```
[00:01] Them: ...
[01:37] Me: ...
```
```

### Frontmatter fields

| Key | Type | Values |
|---|---|---|
| `title` | string | free text |
| `date` | string | `YYYY-MM-DD HH:MM` local |
| `attendees` | list of string | may be empty |
| `sharing` | enum | `full` = Claude wrote the notes · `local` = transcript only, never sent · `none` = should not exist, audio was deleted |
| `capture` | enum | `ok` \| `warn` |
| `warnings` | list of string | human-readable capture problems |

A note with `sharing: local` has a transcript and **no** AI sections.

`qn pending` lists a recording that has no `summary.md`, whatever its consent
says, and skips the recording in progress. Asking the consent instead hid a
meeting completely: a `full` answer that arrives after the watch stopped
waiting leaves no notes and a consent that reads `full`.

`consent` is written with `local` before the recorder captures a sample, so a
crash cannot leave a shareable meeting. That means its presence says nothing
about whether anyone has answered, and `consent.answered` is what says so. A
watch waits `QN_CONSENT_WAIT` seconds, default 90, for that marker after the
meeting stops, and holds the meeting when it never appears.

**Sharing fails closed, everywhere.** A note is indexed only when its
frontmatter positively reads `sharing: full`. Missing, malformed, unknown,
commented, duplicated, or unterminated — all mean private. The same rule
applies to the `consent` file: anything not exactly `full`, `local`, or
`none` is read as `local`.

Frontmatter scalars and list items are always double-quoted, and control
characters are stripped from them. A newline in a title would otherwise close
the block early and drop `sharing:` into the body.

## Transcript line format

```
[MM:SS] Me: text
[MM:SS] Them: text
```

Minutes may exceed 59 (`[104:12]`). Speaker is exactly `Me` or `Them`.

## CLI surface

```
qn record [title...]            record until Ctrl-C, then process
qn redo <id|dir>                rebuild notes from an existing recording
qn --notes-only redo <id>       rebuild the notes, reusing the transcript
qn play <id> [MM:SS]            play the audio from a timestamp
qn approve <id>                 send a held (local) meeting to Claude
qn pending                      list meetings that have no notes yet
qn watch                        auto-record detected meetings
qn index                        rebuild ~/Meetings/.index.db
qn vocab                        rebuild and show the learned vocabulary
qn people                       rebuild and show the roster of who you meet
qn confirm <id> <letter> <name> say who a voice group really was
qn voices                       show the recognisable voices, and the unnamed
qn forget <name>                drop a voice from the roster
qn prune [--older-than 30d]     delete audio older than N days, keep the notes
qn doctor                       check dependencies and permissions
qn doctor --mic                 record a few seconds and measure them
```

A subcommand is recognised only when the argument count matches it, so a
meeting called "index review with priya" records rather than reindexing.

`--notes-only` reuses `them.json` and `me.json` instead of running whisper
again. The words do not change because `prompt.md` did, so a prompt edit costs
seconds rather than a second pass over the audio. It refuses a recording that
has audio but no words yet, because dropping a speaker's whole side would look
like a meeting where nobody spoke. It is refused on a new recording, where
there is nothing to reuse.

## The roster

`~/Meetings/people.md` names the people you meet. One person per line:

```
- **Priya Sharma** (4 meetings, last 2026-08-22) — my manager, owns billing
```

An address in angle brackets claims that address for that person:

```
- **Marco** <mciccone@example.com> (2 meetings, last 2026-08-27) — mobile SDKs
```

A calendar usually gives an address, and no rule turns `mciccone@` into
"Marco". Without the claim the attendee reads as "Dciccale", which matches no
roster entry, so what the user wrote never reaches Claude and every line stays
"Them". The claim is used in three places: the context block, the `attendees:`
in the note, and therefore the attendee filter in search. Angle brackets are
stripped from a calendar name, so an invite cannot forge one.

The bracket is generated and gets rewritten after every recording. The text
after the dash is the user's and is never overwritten. So is the claim: harvest
never produces one and merge never drops one. `qn` sends the entries
matching this meeting's attendees to Claude with the transcript.

Two rules hold:

  Only `sharing: full` notes contribute a name, because the roster is fed back
  to Claude. A held meeting keeps its attendees to itself.

  Every attendee reaches the prompt, whether the roster knows them or not.
  `prompt.md` forbids a name that is not on that list, so an incomplete list
  would silently suppress a real name.

A deleted person is recorded in `.people-removed` and never re-added. A person
typed in by hand always survives, even one deleted before.

## Retention

Audio is over 99% of what the tool stores, at about 70 MB per hour. The note
already holds the full transcript, so `qn prune` deletes `them.m4a` and
`me.m4a` and nothing else.

The recording in progress, named by `.recordings/.recording`, is never pruned.

A meeting whose consent is not `full` is kept unless you say otherwise. `qn
prune` counts the held meetings whose audio is old enough, names the number,
and asks once. Anything but `y` leaves them alone. Without a terminal to ask
at the answer is no, so a cron job or a pipe never deletes held audio, and
`auto_prune` never asks and so never takes it. `QN_ASSUME_YES` answers yes,
the way `QN_CONSENT` answers for a recording.

A held meeting with no `them.json` or `me.json` keeps its audio whatever the
answer. Nothing would be left to approve.

A pruned recording gets a `.pruned` marker. `qn redo` and `qn approve` read it,
say the audio is gone, and rebuild from `them.json` and `me.json`, which
pruning never touches. The capture verdict is copied from the existing note
rather than measured again, because the audio it described no longer exists and
`health.py` would report both tracks missing. A pruned recording that was never
transcribed is refused, because nothing is left to rebuild from.
`QN_DRY_RUN=1` reports what it would delete and deletes nothing.

`auto_prune = yes` runs the same code after a recording, at most once a day.
`.recordings/.last-prune` holds the date of the last automatic run, and is
written before the prune, so a recurring error cannot cause a scan after every
meeting. An automatic run prints what it deleted and stays silent when it
deleted nothing.

An MCP reply is capped at `index.MAX_LIMIT` rows whatever the caller asks for,
and every capped reply reports the real `total` beside `shown`.

## Layout

```
qn                the command, and the only entry point
prompt.md         the note template
lib/              health.py, merge.py, vocab.py, people.py
mcp/              index.py, server.py
recorder/         main.swift, watcher.swift, and their plists
test/             every test_*.py, plus run.sh and the fixtures
```

Every test file lives in `test/`, so `test/run.sh` finds them in one call.
`unittest discover` does not recurse into a directory that is not a package,
so a test file anywhere else would run nowhere and say nothing about it. The
suite also fails when a module in `lib/` or `mcp/` has no `test_<name>.py`
beside it.

## Voice grouping

`diarize = yes` runs `lib/diarize.py` over `them.m4a` before transcription and
writes `speakers.json` into the recording directory. `merge.py` reads it and
labels a `them` line `Them A`, `Them B` by longest overlap. The `me` track is
never relabelled; it is already its own file.

sherpa finds where each stretch of talking starts and stops. The grouping is
ours. Its own clustering is discarded, and `THRESHOLD` stays only because it
shapes those boundaries.

The labels are a hint for Claude, never an identity. `prompt.md` states that
the words win when they disagree with the letter. Grouping alone never becomes
a name: the only route from a letter to a person is a name you confirmed, here
or in an earlier meeting. See **Voice recognition** below.

Grouping runs in three steps. Spans of `RELIABLE_SECONDS` or more are measured
and merged while they stay `MERGE_SIMILARITY` alike. A group under
`MIN_SPEAKER_SECONDS` is dropped. Every span, long or short, then joins the
group it most resembles — but only if it reaches `MERGE_SIMILARITY`. A span that
reaches nothing keeps the plain `Them` label, which is the honest answer for a
two-second reply that could be anybody. Letters go out busiest voice first,
and every group that survives gets one — the alphabet is the only cap.

Short spans are the reason for the split. Their vectors resemble each other more
than they resemble their own speaker, so left in the clustering they form a junk
group that swallows everybody's brief replies. They get a seat, never a vote.

`qn confirm <id> <letter> <name>` records who a voice was, in
`confirmed.txt` beside the audio as `A=Marco`. `merge.py` reads it and writes
the name instead of the letter, so a `qn redo` keeps the answer. The letter is
checked against `speakers.json`, not the transcript, so a confirmation can be
corrected after it has already replaced the label.

## Voice recognition

`diarize.py` also writes `voiceprints.json`: one vector per letter, built from
up to `PRINT_SECONDS` of that letter's longest segments. It is a separate file
because `speakers.json` holds one row per time segment.

`voiceprint.onnx` does both jobs — grouping inside a meeting and recognition
across them. `embedding.onnx` remains because sherpa will not run its
segmentation without an embedding model, and which one it gets moves the span
boundaries: on a 26-minute standup it produced 318 spans against
`voiceprint.onnx`'s 240, in half the time. Its groups are discarded; its
boundaries are not.
`voiceprint.onnx` is optional: without it there is no grouping and no
`voiceprints.json`, and the transcript reads plain `Them` throughout.

Six models were measured on nine real recordings, four with a speaker
identified away from the audio. Same audio, same groups, same arithmetic:

| model | size | worst same person | best different people | gap |
|---|---|---|---|---|
| wespeaker CAM++ voxceleb | 29 MB | 0.879 | 0.931 | −0.051 |
| wespeaker CAM++_LM | 29 MB | 0.793 | 0.933 | −0.140 |
| wespeaker resnet293_LM | 114 MB | 0.980 | 0.973 | +0.007 |
| 3dspeaker eres2netv2 | 71 MB | 0.744 | 0.553 | +0.191 |
| 3dspeaker campplus zh_en | 28 MB | 0.826 | 0.614 | +0.212 |
| **nemo titanet_large** | 101 MB | 0.755 | 0.406 | **+0.349** |

A negative gap means no threshold is both safe and useful. The clustering
model is the worst of the six at recognition: it rated two colleagues more
alike than one colleague against himself. Size does not predict quality.
`MATCH_THRESHOLD = 0.65` sits about two thirds from 0.406 up to 0.755, which
leans towards saying nothing rather than saying a name.

### Measuring the grouping

Three meetings were labelled by ear, blind to the letters: 137 segments named,
mixed and unsure ones excluded rather than guessed. One set the constants; two
were labelled after they were frozen.

| meeting | role | today | with `MERGE_SIMILARITY` |
|---|---|---|---|
| 2026-09-03 standup | tuning | 73.7% | 100% |
| 2026-08-25 backlog | held out | 85.5% | 100% |
| 2026-09-01 standup | held out | 60.0% | 100% |

Purity is generous to the old design: every letter is allowed to be renamed to
whichever person appears in it most. The old grouping still lost, because it
split each person across several letters and mixed several people into one.

`0.50` was never fitted to the held-out meetings. On the first the closest two
people scored 0.421 and one person at worst 0.532; on the second, 0.370 and
0.591. The gap held both times.

The floor earns its place on the spans a person cannot judge either. Five clips
were too brief to identify by ear; not one reached 0.50, and they averaged
0.269 against 0.685 for the clips that were identifiable. Without the floor all
five would have been given a name.

`MAX_SAMPLES` keeps the newest ten samples of a person. Not for space — fifty
cost 52 KB and match in 0.1 ms — but for drift, since every sample weighs the
same in the mean. `enrol_from` reports when a sample was dropped for being
older than the ten kept, so a confirm never claims a success it did not have.

A voice must talk `MIN_PRINT_SECONDS` before it gets a print. Below that it
keeps its letter, keeps its audio for `qn play`, and is never offered for
naming. The floor is enforced in three places — when prints are built, in
`pending`, and in `enrol_from` — because a file written by an earlier version
still holds prints for short voices.

`lib/voices.py` keeps the roster in `<notes>/.voices.json`:

```json
{"version": 1, "people": {"Aisha": [{"from": "<id>", "voice": "A", "vector": []}]}}
```

One entry per voice, keyed by meeting **and** letter. The grouping splits one
person across letters, so a single meeting can hold three good samples of the
same colleague. An entry with no `voice` comes from the first version and reads
as an empty letter. A person's stored voice is the mean of their entries, and
`match` is cosine similarity against that mean — the same figure sherpa-onnx's
`SpeakerEmbeddingManager` computes, written out so the module stays
stdlib-only and its tests need no model.

`qn confirm` enrols the voiceprint after writing `confirmed.txt`, then re-runs
the match over the same meeting. Naming A therefore names B and C when they are
the same person, and they leave the waiting list. Enrolling under a new name
removes that meeting-and-letter entry from whoever held it, so a correction
stops the mistake voting. A meeting whose `consent` is not `full` enrols
nothing, and `qn confirm` says so.

Every rebuild re-runs the match and rewrites `matched.txt` from scratch, never
adding to it: after `qn forget`, the next rebuild must drop that name. A letter
present in `confirmed.txt` is skipped, so re-matching cannot undo your answer.

`qn voices` lists the roster and the voiceprints with no name. `qn forget
<name>` drops a person. Neither touches a note already written.

`qn play <id> <letter>` plays one voice group from `them.m4a` alone, using the
spans `print_ranges` chose for the voiceprint, joined with `aselect` and
`asetpts`. It is the audio the roster learned from, so a bad cluster can be
heard rather than guessed at. The dispatch reads a single letter as a voice and
anything longer as a meeting title; `<MM:SS>` keeps its existing meaning of
both tracks mixed from that moment.

Every note carries what it knows:

```yaml
speaker_map: ["A: Marco (confirmed)", "B: Aisha (matched)", "C: Lena (guess)"]
```

`(guess)` comes from the `## Speakers` section Claude writes, which `qn` lifts
out of the body into the frontmatter, and it never names a transcript line.
`(matched)` comes from the roster. `(confirmed)` comes from `qn confirm` and
outranks both. `merge.py` applies the same order to the transcript itself.

Everything is optional. Without `make diarize` the models are absent, the step
is skipped, and the transcript is byte-identical to one produced without it.

## Settings

`~/.config/quiet-notetaker/config` holds `key = value` lines. `#` starts a
comment. `QN_CONFIG` names another file, which is how the tests stay away from
the developer's own.

| Key | Environment override | Default |
|---|---|---|
| `notes` | `QN_NOTES_DIR` | `~/Meetings` |
| `prune_days` | `QN_PRUNE_DAYS` | `30` |
| `auto_prune` | `QN_AUTO_PRUNE` | `no` |
| `diarize` | `QN_DIARIZE` | `no` |
| `language` | `QN_LANG` | `en` |
| `play_window` | `QN_PLAY_WINDOW` | `60` |

Precedence is environment, then file, then default, everywhere including the
MCP server. Claude starts the server itself, so the server never sees the
shell's environment; the file is the only channel that reaches both sides.

`qn` resolves its own symlinks before locating `models/`, `build/` and its
helper scripts, so it works from any directory once linked onto `PATH`.

`qn doctor --mic` records `QN_MIC_SECONDS` seconds, default 8, and reads the
result with `health.advise()`. That is a separate judgement from `health.judge()`
on purpose: a meeting with a silent `them` track is broken, and a microphone
test with one is working, because nothing was playing. It is not part of a
plain `qn doctor`, which runs during `make install` before any permission has
been granted, and which must never record the user unasked. It exits non-zero
on any `fail`.

`qn doctor` also counts the notes on disk, how many are indexed, and how many
are held. It calls `index.audit()`, which calls `parse_note()` — the same
function the server calls — so the count can never disagree with what a search
returns. This is reported by `qn` and not by the MCP server on purpose: a held
meeting is invisible to every tool there, so Claude cannot enumerate what it is
hiding. The roster is not counted as a meeting.

## Watcher event protocol

`build/watcher` prints one event per line to stdout, flushed immediately:

```
START<TAB>app=zoom.us<TAB>window=Zoom Meeting<TAB>title=SDK Sync<TAB>attendees=Priya, Arjun
STOP
```

`title` and `attendees` are present only when a calendar event overlaps now.
Values never contain tabs or newlines. Unknown fields are ignored by readers.

`qn watch` writes up a finished meeting behind itself, one at a time. Doing it
inline left the loop unable to read its own event pipe: the next meeting was
stamped with the clock time the loop reached it, was asked for consent after
it had ended, and captured nothing.

## MCP tools

Read-only. Four tools. All return JSON.

### `meetings_search`
`{query: string, from?: "YYYY-MM-DD", to?: "YYYY-MM-DD", with?: string, limit?: int=10}`
→ `{total: int, shown: int, results: [{id, title, date, attendees, matched_in, snippet}]}`
`matched_in` is `notes` or `transcript`. Never returns whole documents.

### `meetings_get`
`{id: string, section?: string}`
→ `{id, title, date, attendees, sharing, sections: {name: text}}`
Returns the notes only. Never the transcript.

### `meetings_transcript`
`{id: string, around?: "MM:SS", window?: int=60}`
→ `{id, title, lines: [string], truncated: bool}`
`around` returns only lines within `window` seconds of that timestamp.

### `meetings_actions`
`{status?: "open"|"done"|"all"=open, whose?: "mine"|"theirs"|"all"=mine, from?, to?, limit?: int=50}`
→ `{total, shown, items: [{id, date, meeting_title, whose, owner, text, done}]}`

## Testing

- Python: `unittest`, stdlib only. No pytest, no third-party imports anywhere.
- Shell: `test/run.sh`, plain bash asserts.
- Swift: both binaries take `--self-test`, exercising pure logic with no
  hardware, printing one line per case and exiting non-zero on any failure.
- Fixtures are generated, never committed as binaries.
- `make test` runs everything and exits non-zero on any failure.
