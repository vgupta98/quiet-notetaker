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
make                 # builds the binaries, downloads the models (~466 MB)
./qn doctor          # checks everything is in place
```

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
```

Every recording is judged before transcription. A missing track, a silent
track, a clipping mic, or one track stopping early is reported in the terminal
and recorded in the note. The tool will not pretend a bad recording is a
meeting.

## Asking Claude about your meetings

```sh
claude mcp add quiet-notetaker -- python3 "$PWD/mcp/server.py"
```

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
| `recorder/main.swift` | Captures both tracks with ScreenCaptureKit |
| `recorder/watcher.swift` | Detects meetings starting and ending |
| `qn` | The command you actually use |
| `health.py` | Decides whether a recording is usable |
| `merge.py` | Interleaves the two transcripts by timestamp |
| `vocab.py` | Learns your meetings' words and primes whisper with them |
| `people.py` | Keeps the roster of who you meet, and what you wrote about them |
| `prompt.md` | The note template. Edit this to change the notes |
| `mcp/` | The search index and the MCP server |

Recordings stay in `~/Meetings/.recordings/`, about 24 MB per hour. Delete them
whenever you like — the notes keep the full transcript.

## Settings

| Variable | Default |
|---|---|
| `QN_NOTES_DIR` | `~/Meetings` |
| `QN_MODEL` | `models/ggml-small.en.bin` |
| `QN_LANG` | `en` |
| `QN_CONSENT` | unset — asks every time. Set to `full`/`local` to stop asking |

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
