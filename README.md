# Quiet Notetaker

Records your meetings on your Mac, then writes the summary and the action items.
Nothing joins the call. No audio leaves the machine.

Design: https://claude.ai/code/artifact/e7d7b11d-56c5-4c67-8fb6-4e497fa553e9

## Setup

```sh
brew install ffmpeg whisper-cpp
make            # builds the recorder, downloads the speech models (~466 MB)
```

The first recording asks for two macOS permissions: **Screen Recording** (this is
how macOS lets an app hear other apps) and **Microphone**. Grant both to the app
you run `qn` from — Terminal, iTerm, or Claude Code.

## Use

```sh
./qn sdk sync        # start recording, Ctrl-C to stop
```

Name who is in the meeting, so the notes say who agreed to what:

```sh
./qn --with "Priya, Arjun" sdk sync
```

Claude works out who is speaking from how people address each other. It writes
"Them" when it cannot tell, and it never uses a name you did not give it.

Recording stops, and then it transcribes and writes the notes on its own. The
note lands in `~/Meetings/2026-08-20-1430-sdk-sync.md`.

To rebuild the notes for a meeting you already recorded — after editing
`prompt.md`, for example:

```sh
./qn redo ~/Meetings/.recordings/2026-08-20-1430-sdk-sync
```

## How it works

Your Mac records two separate tracks: everything the meeting app plays
(`them.m4a`) and your microphone (`me.m4a`). Keeping them apart is what gives the
transcript speaker labels, with no speaker-identification model involved.

The microphone track gets its level evened out first. Your voice moves around;
the meeting app's audio does not. That one filter makes a large difference to
the microphone transcript, and it makes the system track worse, so it runs on
the microphone only.

Whisper turns each track into text on your Mac. `merge.py` puts both back into
one conversation in the order it was said. The `claude` CLI then reads that and
fills in the template in `prompt.md`.

```
them.m4a ─┐
          ├─ whisper ─→ merge ─→ transcript ─→ claude ─→ ~/Meetings/*.md
me.m4a   ─┘
```

## Layout

| Path | What it does |
|---|---|
| `recorder/main.swift` | Captures both audio tracks with ScreenCaptureKit |
| `qn` | Records, then runs everything after |
| `merge.py` | Interleaves the two transcripts by timestamp |
| `prompt.md` | The note template. Edit this to change what the notes contain |

The transcript is kept exactly as whisper heard it. Claude corrects mishearings
in the notes above it, so you can always check what was really said.

Recordings stay in `~/Meetings/.recordings/`, about 24 MB per hour. Delete them
whenever you want — the notes keep the full transcript.

## Settings

| Variable | Default |
|---|---|
| `QN_NOTES_DIR` | `~/Meetings` |
| `QN_MODEL` | `models/ggml-small.en.bin` |
| `QN_VAD_MODEL` | `models/ggml-silero-v5.1.2.bin` |

For better accuracy on hard audio, swap the model:
`make models MODEL_NAME=ggml-medium.en.bin` then set `QN_MODEL` to it.
