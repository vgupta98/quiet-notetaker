# Contract

Everything in this repo agrees on the shapes below. Change them here first.

## Layout

```
~/Meetings/
  2026-08-20-1535-sdk-sync.md          the note. source of truth.
  .recordings/2026-08-20-1535-sdk-sync/
      them.m4a  me.m4a  them.json  me.json  transcript.txt  summary.md
      consent                              one word: full | local | none
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
qn [--with "A, B"] [--local] [title...]   record until Ctrl-C, then process
qn redo <id|dir>                rebuild notes from an existing recording
qn play <id> [MM:SS]            play the audio from a timestamp
qn approve <id>                 send a held (local) meeting to Claude
qn pending                      list meetings awaiting approval
qn watch                        auto-record detected meetings
qn index                        rebuild ~/Meetings/.index.db
qn vocab                        rebuild and show the learned vocabulary
qn doctor                       check dependencies and permissions
```

A subcommand is recognised only when the argument count matches it, so a
meeting called "index review with priya" records rather than reindexing.

`QN_NOTES_DIR` overrides `~/Meetings` everywhere, including the MCP server.

## Watcher event protocol

`build/watcher` prints one event per line to stdout, flushed immediately:

```
START<TAB>app=zoom.us<TAB>window=Zoom Meeting<TAB>title=SDK Sync<TAB>attendees=Priya, Arjun
STOP
```

`title` and `attendees` are present only when a calendar event overlaps now.
Values never contain tabs or newlines. Unknown fields are ignored by readers.

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
