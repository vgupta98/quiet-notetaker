# Quiet Notetaker

Records your meetings on your Mac, writes the summary and the action items, and
lets Claude answer questions across every meeting you have ever had.

No bot joins the call. No audio ever leaves the machine. Your notes are plain
markdown in a folder you own — no database, nothing to be locked out of.

Design: https://claude.ai/code/artifact/e7d7b11d-56c5-4c67-8fb6-4e497fa553e9
Contract: [SPEC.md](SPEC.md)

## Setup

```sh
brew install ffmpeg whisper-cpp
make install
```

`make install` builds the binaries, downloads the models (~466 MB), puts `qn`
on your PATH, and runs `qn doctor`. Then `qn` works from any folder:

```sh
cd ~/anywhere
qn team sync
```

The link points back at this checkout, so `git pull` upgrades you. `make
uninstall` removes the link and never touches your notes.

Grant **Screen Recording** to the app you run `qn` from — that is how macOS
lets an app hear other apps. **Microphone** and **Calendar** are requested on
first use.

## Use

```sh
./qn sdk sync                        # record, Ctrl-C to stop
./qn --with "Priya, Arjun" sync      # name who is there
./qn --local 1:1 with manager        # transcribe, but never send to Claude
./qn watch                           # record detected meetings by itself
```

`watch` starts recording the moment a call begins, then asks how to handle it.
The recording never waits for your answer, so you never lose the first minute.

| Answer | What happens |
|---|---|
| **Full notes** | Transcript goes to Claude, notes are written |
| **Local only** | Transcript stays on this Mac. No AI notes |
| **Do not record** | Audio is deleted |
| *no answer* | Held locally — nothing is sent |

Held meetings wait for you:

```sh
./qn pending                         # what is waiting
./qn approve 2026-08-20-1535-sync    # send it now
```

### Checking and fixing

```sh
./qn play 2026-08-20-1535-sync 01:37   # hear what was actually said
./qn redo 2026-08-20-1535-sync         # rebuild notes after editing prompt.md
./qn --notes-only redo 2026-08-20-1535-sync   # same, without listening again
```

`redo` listens to the whole recording again, which takes minutes. Editing
`prompt.md` changes how the notes read, not what anyone said, so `--notes-only`
reuses the words from last time and rebuilds only the notes. That takes seconds.

Use plain `redo` only when the audio must be heard again: after you add a word
to your vocabulary, or after you turn on voice grouping. A `qn confirm` needs
no such thing, because the name is applied when the two tracks are stitched
back together, which `--notes-only` still does.

Before a meeting that matters, check the microphone actually carries sound:

```sh
qn doctor --mic          # records 8 seconds, then tells you what it heard
```

`qn doctor` proves the permissions are granted. Only this proves your voice is
reaching the tool, which is the one failure that costs a whole meeting.

```
  ✓ your microphone works (-28 dB average)
  ✓ system audio is being captured — nothing was playing, so play something
    during the test to check it fully
```

Every recording is judged before transcription. A missing track, a silent
track, a clipping mic, or one track stopping early is reported in the terminal
and recorded in the note. The tool will not pretend a bad recording is a
meeting.

## Asking Claude about your meetings

```sh
claude mcp add quiet-notetaker -- python3 "$PWD/mcp/server.py"
```

The server finds your notes through `~/.config/quiet-notetaker/config`, so a
custom notes folder needs nothing extra here.

For Claude Desktop, add this to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "quiet-notetaker": {
      "command": "python3",
      "args": ["/absolute/path/to/quiet-notetaker/mcp/server.py"]
    }
  }
}
```

Then ask normally:

> what are my pending action items this week?
> what did we decide about the Kotlin release?
> did anyone mention Amplitude in the last month?

Four read-only tools back this: `meetings_search`, `meetings_get`,
`meetings_transcript`, `meetings_actions`. Search returns pointers and
snippets, never whole documents, so a question costs a few hundred tokens
rather than your whole context. Claude can read your notes. It cannot change
them.

Meetings marked **local only** are never indexed and never appear in a result.

## What it learns

Whisper guesses at words nobody told it about. "On-call" came out as "uncle" in
every note until Claude fixed it afterwards. So the tool builds a vocabulary
from your own meetings and primes the decoder with it.

```sh
./qn vocab      # show what it has learned
```

The list lives in `~/Meetings/vocabulary.txt`. Open it, edit it, add your own
words. **Delete a line and it never comes back.**

It cannot poison itself. A vocabulary that learned from whisper's own output
would reinforce its own mistakes until they were all you saw, so that path does
not exist. Only two things get in:

| Source | Rule |
|---|---|
| Attendee names | Typed by you or read from your calendar. Never produced by the decoder, so trusted on sight |
| Words Claude corrected | Present in the notes, absent from that meeting's transcript. Needs two different meetings before it counts |

**Any spelling whisper produced anywhere is disqualified everywhere.** "Uncle"
is in the transcript, so it can never be learned. "On-call" only exists because
Claude put it there, so it can.

Private meetings contribute nothing.

## Who said what

The transcript labels two speakers: `Me` from your microphone, and `Them` from
everything the call played back. `Them` is a mixdown of every other voice, so
no audio work can split it into people. The names come from context instead.

Claude does that better when it knows who these people are. So the tool keeps a
roster:

```sh
./qn people     # show who you meet
```

`~/Meetings/people.md` holds one person per line:

```
- **Priya Sharma** (4 meetings, last 2026-08-22) — my manager, owns billing
```

**The bracket is ours. Everything after the dash is yours.** Write who someone
is, and that text goes to Claude whenever that person is in a meeting. It is
what turns a `Them` line into a name.

- Only the attendees of *this* meeting are sent. Nobody else is named.
- Only `sharing: full` meetings add a name, because the roster goes back to Claude.
- Delete a line and that person never comes back. Add your own freely.

Names are never guessed from audio. They come from `--with` or from your
calendar, so a person typed them.

### Grouping the other voices (optional)

`Them` is a mixdown of everyone else, so it is one label for a whole room. You
can group it by voice:

```sh
make diarize        # 49 MB environment, 28 MB of models, both MIT
```

Then in your settings file:

```
diarize = yes
```

The transcript gains `Them A:`, `Them B:` labels, and Claude uses them
alongside the roster to work out who said what.

**These letters are a hint, not an identity, and the tool will not turn them
into names by itself.** Measured on a real 26-minute standup, the clustering
put a question and its answer in the same group — two people, one letter.
`prompt.md` tells Claude to believe the words over the letter when they
disagree, so a bad group can be overruled. An automatic name could not be.

It costs about seven minutes of processing per hour of audio, on top of the
transcript. That is why it is off by default.

### Saying who a voice was

You were in the room. Nothing else here can say that:

```sh
qn confirm 2026-08-24-1500-sdk-standup A "Marco"
```

From then on that voice is **Marco** in the transcript, not `Them A`. Your
answer is kept beside the audio, so `qn redo` never loses it, and you can
correct it by confirming the same letter again.

Every note records what it knows in its frontmatter:

```yaml
speaker_map: ["A: Marco (confirmed)", "B: Lena (guess)"]
```

`(guess)` is Claude reading the conversation. `(confirmed)` is you. Only a
confirmation puts a name on a transcript line.

## How it works

Your Mac records two tracks: everything the meeting app plays (`them.m4a`) and
your microphone (`me.m4a`). Keeping them apart is what gives the transcript
speaker labels, with no speaker-identification model involved.

The microphone track is levelled first. Your voice moves around; the meeting
app's audio does not. That one filter makes a large difference to the mic
transcript, and it makes the system track worse, so it runs on the mic only.

Whisper turns each track into text on your Mac. `merge.py` interleaves them
back into one conversation. Claude then fills in the template in `prompt.md`.

```
them.m4a ─┐
          ├─ whisper ─→ merge ─→ transcript ─→ claude ─→ ~/Meetings/*.md
me.m4a   ─┘                                                    │
                                                          FTS5 index ─→ MCP ─→ Claude
```

The transcript is stored exactly as whisper heard it. Claude corrects
mishearings in the notes above it, so a wrong correction is always checkable.

## Layout

| Path | What it does |
|---|---|
| `qn` | The command you actually use |
| `prompt.md` | The note template. Edit this to change the notes |
| `Makefile` | `make install` puts `qn` on your PATH |
| `recorder/main.swift` | Captures both tracks with ScreenCaptureKit |
| `recorder/watcher.swift` | Detects meetings starting and ending |
| `lib/health.py` | Decides whether a recording is usable |
| `lib/merge.py` | Interleaves the two transcripts by timestamp |
| `lib/vocab.py` | Learns your meetings' words and primes whisper with them |
| `lib/people.py` | Keeps the roster of who you meet, and what you wrote about them |
| `lib/names.py` | Reads a person out of a calendar invite's email address |
| `lib/diarize.py` | Optional. Groups the `them` track by voice |
| `mcp/` | The search index and the MCP server |
| `test/` | Every test, and the harness that runs them |

## Keeping it small

Audio is the only thing that really grows — about 70 MB per hour, two tracks at
96 kbps. The notes and transcripts are about 42 KB per hour, so **forty years of
transcripts fit in under a gigabyte.**

```sh
qn prune                          # delete audio older than 30 days
qn --older-than 7d prune          # be stricter
QN_DRY_RUN=1 qn prune             # see what it would delete
```

To stop thinking about it, turn on housekeeping in your settings file:

```
auto_prune = yes
prune_days = 30
```

It then runs itself after a recording, **at most once a day**, using the same
code as `qn prune` — so the same meetings are protected. It says what it
deleted and stays quiet when there is nothing to do. It is off until you ask.

It deletes the two `.m4a` files and nothing else. You keep the notes, the
transcript, and the record of what you consented to. The only thing you lose is
`qn play` — hearing the real voice.

`qn approve` still works on a pruned meeting. It rebuilds from the words, which
survive pruning, and keeps the capture verdict from the note rather than judging
audio that is no longer there.

It never prunes the meeting being recorded right now.

A meeting still waiting for your approval is kept unless you say otherwise.
`qn prune` counts them, tells you what deleting their audio costs, and asks:

```
  2 meeting(s) awaiting approval have audio older than 30 days
  deleting it is safe: 'qn approve' rebuilds them from the words it kept
  you lose only 'qn play' — hearing the real voice

  delete their audio too? [y/N]
```

Anything but `y` leaves them alone. A run with nobody to ask — a script, a
scheduled job, `auto_prune` — always answers no. And a held meeting that was
never transcribed keeps its audio whatever you say, because nothing would be
left to approve.

Searching does not grow either. `meetings_search` returns pointers and short
snippets, never documents, so a question costs the same context whether you
have ten meetings or ten thousand.

## Settings

Settings live in `~/.config/quiet-notetaker/config`, one `key = value` per
line. Create it when you want to change something:

```
# where your meetings are kept
notes = ~/Documents/notes/meetings

auto_prune  = yes
prune_days  = 14
language    = en
play_window = 60
```

**Put the notes folder here, not in your shell.** Claude starts the MCP server
itself, so an `export` in `~/.zshrc` never reaches it. The settings file is the
one place both sides read. `qn doctor` shows the folder in use and where the
setting came from:

```
notes:    /Users/vishalgupta/Documents/notes/meetings
set by:   /Users/vishalgupta/.config/quiet-notetaker/config
```

It also counts what Claude can and cannot see:

```
meetings: 38
indexed:  34 visible to Claude
held:      4 private — never sent
```

A held meeting is invisible to the search tools by design, so asking Claude
what it is hiding proves nothing. This counts your files directly, with the
same code the search uses.

It also warns when `~/Meetings` still holds notes that nothing reads any more.

Every setting has an environment variable that overrides the file, for one-off
runs and for scripts:

| Variable | Setting | Default |
|---|---|---|
| `QN_NOTES_DIR` | `notes` | `~/Meetings` |
| `QN_PRUNE_DAYS` | `prune_days` | `30` |
| `QN_AUTO_PRUNE` | `auto_prune` | `no` |
| `QN_DIARIZE` | `diarize` | `no` |
| `QN_LANG` | `language` | `en` |
| `QN_PLAY_WINDOW` | `play_window` | `60` |
| `QN_MODEL` | — | `models/ggml-small.en.bin` |
| `QN_CONSENT` | — | unset — asks every time. Set to `full`/`local` to stop asking |
| `QN_CONFIG` | — | `~/.config/quiet-notetaker/config` |

Better accuracy on hard audio:
`make models MODEL_NAME=ggml-medium.en.bin`, then set `QN_MODEL`.

## Tests

```sh
make test
```

Runs unit tests for the pure logic, the watcher's state-machine self-test, and
CLI tests against generated fixtures. It never touches your real `~/Meetings`.

## Requirements

macOS 15 or later, Apple Silicon. Microphone capture through ScreenCaptureKit
is new in macOS 15, and the binaries are built for `arm64`.
