#!/usr/bin/env bash
# The whole test suite. Exits non-zero when anything fails.
#
#   test/run.sh
#
# Everything runs against a throwaway QN_NOTES_DIR. The suite refuses to run
# against ~/Meetings. Set QN_KEEP=1 to leave the temp tree behind.
#
# Bash 3.2 compatible: no associative arrays, no ${var^^}, no `set -e`.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
QN="$ROOT/qn"

# Keep the run from dropping __pycache__ directories into the repo.
export PYTHONDONTWRITEBYTECODE=1

# No test may read the developer's own settings file. A path that cannot
# exist makes every config lookup fall through to the built-in default.
export QN_CONFIG="/nonexistent/quiet-notetaker/config"

# --------------------------------------------------------------------------
# assert harness
# --------------------------------------------------------------------------
PASSED=0; FAILED=0; SKIPPED=0
if [ -t 1 ]; then
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; N=$'\033[0m'
else
  G=''; R=''; Y=''; D=''; B=''; N=''
fi
section() { printf '\n%s%s%s\n' "$B" "$1" "$N"; }
pass() { PASSED=$((PASSED + 1)); printf '  %sok%s    %s\n' "$G" "$N" "$1"; }
fail() { FAILED=$((FAILED + 1)); printf '  %sFAIL%s  %s\n' "$R" "$N" "$1"
         if [ $# -gt 1 ]; then printf '        %s%s%s\n' "$D" "$2" "$N"; fi; }
skip() { SKIPPED=$((SKIPPED + 1)); printf '  %sskip%s  %s %s(%s)%s\n' "$Y" "$N" "$1" "$D" "$2" "$N"; }
assert_eq()       { if [ "x$1" = "x$2" ]; then pass "$3"; else fail "$3" "want [$1] got [$2]"; fi; }
assert_contains() { case "$1" in *"$2"*) pass "$3";; *) fail "$3" "output has no [$2]";; esac; }
assert_missing()  { case "$1" in *"$2"*) fail "$3" "output should not have [$2]";; *) pass "$3";; esac; }
assert_file()     { if [ -s "$1" ]; then pass "$2"; else fail "$2" "no such file, or empty: $1"; fi; }
assert_exit() { # assert_exit <want-code> <name> <cmd...>
  local want="$1" name="$2"; shift 2
  "$@" >/dev/null 2>&1
  local got=$?
  if [ "$got" -eq "$want" ]; then pass "$name"; else fail "$name" "want exit $want, got $got"; fi
}
CAPTURE_CODE=0
capture() { # capture <timeout-secs> <outfile> <cmd...>; sets CAPTURE_CODE (124 on timeout)
  local secs="$1" file="$2" mark="$2.timeout"; shift 2
  rm -f "$mark"
  "$@" >"$file" 2>&1 &
  local pid=$!
  ( sleep "$secs"; if kill -0 "$pid" 2>/dev/null; then : > "$mark"; kill -9 "$pid" 2>/dev/null; fi ) >/dev/null 2>&1 &
  local dog=$!
  wait "$pid" >/dev/null 2>&1; CAPTURE_CODE=$?
  kill "$dog" >/dev/null 2>&1; wait "$dog" >/dev/null 2>&1
  if [ -e "$mark" ]; then CAPTURE_CODE=124; fi
}

# --------------------------------------------------------------------------
# temp notes dir, guarded
# --------------------------------------------------------------------------
TMPROOT="$(mktemp -d "${TMPDIR:-/tmp}/qn-test.XXXXXX")"
cleanup() { if [ "${QN_KEEP:-0}" = "1" ]; then printf '\nkept: %s\n' "$TMPROOT"; else rm -rf "$TMPROOT"; fi; }
trap cleanup EXIT

if [ -z "${QN_NOTES_DIR:-}" ]; then QN_NOTES_DIR="$TMPROOT/notes"; fi
export QN_NOTES_DIR

guard_notes_dir() {
  if [ -z "${QN_NOTES_DIR:-}" ]; then
    printf 'run.sh: QN_NOTES_DIR is unset — refusing to run\n' >&2; exit 2
  fi
  case "$QN_NOTES_DIR" in
    "$HOME"|"$HOME"/|"$HOME"/Meetings|"$HOME"/Meetings/*)
      printf 'run.sh: QN_NOTES_DIR points at real data (%s) — refusing to run\n' "$QN_NOTES_DIR" >&2
      exit 2 ;;
  esac
}
guard_notes_dir
mkdir -p "$QN_NOTES_DIR"

printf '%squiet-notetaker test suite%s\n' "$B" "$N"
printf '%snotes dir: %s%s\n' "$D" "$QN_NOTES_DIR" "$N"

# --------------------------------------------------------------------------
section "fixtures"
# --------------------------------------------------------------------------
assert_exit 0 "fixtures.py writes a corpus" python3 "$HERE/fixtures.py" "$QN_NOTES_DIR"

# The phrases and ids come from the module, never from a hardcoded string.
py_const() { python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import fixtures; print(getattr(fixtures, sys.argv[2]))" "$HERE" "$1"; }
NOTES_ONLY_PHRASE="$(py_const NOTES_ONLY_PHRASE)"
TRANSCRIPT_ONLY_PHRASE="$(py_const TRANSCRIPT_ONLY_PHRASE)"
NOTES_ONLY_ID="$(py_const NOTES_ONLY_ID)"
TRANSCRIPT_ONLY_ID="$(py_const TRANSCRIPT_ONLY_ID)"
WARN_ID="$(py_const WARN_ID)"
LONG_ID="$(py_const LONG_TRANSCRIPT_ID)"
LOCAL_ID="$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import fixtures; print(fixtures.LOCAL_IDS[0])" "$HERE")"

FULL_ID="2026-03-04-0930-roadmap-review"
full_note="$QN_NOTES_DIR/$FULL_ID.md"
local_note="$QN_NOTES_DIR/$LOCAL_ID.md"

assert_file "$full_note" "a full note exists"
assert_file "$local_note" "a local note exists"
assert_file "$QN_NOTES_DIR/.recordings/$FULL_ID/consent" "consent file exists"
assert_eq "local" "$(cat "$QN_NOTES_DIR/.recordings/$LOCAL_ID/consent")" "consent says local"

full_text="$(cat "$full_note")"
assert_eq "---" "$(head -1 "$full_note")" "note opens with frontmatter"
assert_contains "$full_text" "title: \"Roadmap Review\"" "frontmatter has a quoted title"
assert_contains "$full_text" "date: 2026-03-04 09:30" "frontmatter date is YYYY-MM-DD HH:MM"
assert_contains "$full_text" "attendees: [\"Priya\", \"Arjun\"]" "frontmatter has a quoted attendee list"
assert_contains "$full_text" "sharing: full" "frontmatter has sharing"
assert_contains "$full_text" "capture: ok" "frontmatter has capture"
assert_contains "$full_text" "warnings: []" "frontmatter has warnings"
for heading in "Summary" "Decisions" "My action items" "Their action items" "Open questions"; do
  assert_contains "$full_text" "## $heading" "section: $heading"
done
assert_contains "$full_text" "## Transcript" "note has a transcript block"
assert_contains "$full_text" "- [ ] " "note has an open action item"
assert_contains "$full_text" "- [x] " "note has a done action item"
assert_contains "$full_text" "- [ ] Priya: " "their item carries an owner prefix"

no_owner_text="$(cat "$QN_NOTES_DIR/2026-07-09-1700-budget-planning.md")"
assert_contains "$no_owner_text" "- [x] Confirm the storage quota" "their item without an owner prefix"

empty_att="$(cat "$QN_NOTES_DIR/2026-08-20-1535-partner-intro.md")"
assert_contains "$empty_att" "attendees: []" "a note has empty attendees"
assert_missing "$empty_att" "_With:" "empty attendees means no With line"

local_text="$(cat "$local_note")"
assert_contains "$local_text" "sharing: local" "local note says sharing: local"
assert_contains "$local_text" "## Transcript" "local note keeps its transcript"
assert_missing "$local_text" "## Summary" "local note has no AI sections"

warn_text="$(cat "$QN_NOTES_DIR/$WARN_ID.md")"
assert_contains "$warn_text" "capture: warn" "a note has capture: warn"
assert_contains "$warn_text" "warnings: [\"microphone" "the warn note lists warnings"

long_lines="$(wc -l < "$QN_NOTES_DIR/.recordings/$LONG_ID/transcript.txt" | tr -d ' ')"
if [ "$long_lines" -gt 100 ]; then pass "long transcript is $long_lines lines"
else fail "long transcript is over 100 lines" "got $long_lines"; fi
assert_contains "$(cat "$QN_NOTES_DIR/.recordings/$LONG_ID/transcript.txt")" "[1" "long transcript passes minute 59"

# Transcript line format: [MM:SS] Me|Them: text, on every line of every file.
bad_lines="$(cat "$QN_NOTES_DIR"/.recordings/*/transcript.txt | grep -cvE '^\[[0-9]{2,}:[0-9]{2}\] (Me|Them): .' )"
assert_eq "0" "$bad_lines" "every transcript line matches the SPEC format"

# The fixtures must agree with the real merger.
merge_bad=0
for work in "$QN_NOTES_DIR"/.recordings/*/; do
  if ! python3 "$ROOT/merge.py" "$work" | diff -q - "$work/transcript.txt" >/dev/null 2>&1; then
    merge_bad=$((merge_bad + 1))
  fi
done
assert_eq "0" "$merge_bad" "merge.py reproduces every transcript.txt"

# Search phrases must stay on their own side of the line.
assert_contains "$(cat "$QN_NOTES_DIR/$NOTES_ONLY_ID.md")" "$NOTES_ONLY_PHRASE" "notes-only phrase is in its note"
assert_eq "" "$(grep -l "$NOTES_ONLY_PHRASE" "$QN_NOTES_DIR"/.recordings/*/transcript.txt 2>/dev/null)" "notes-only phrase is in no transcript"
assert_contains "$(cat "$QN_NOTES_DIR/.recordings/$TRANSCRIPT_ONLY_ID/transcript.txt")" "$TRANSCRIPT_ONLY_PHRASE" "transcript-only phrase is in its transcript"
assert_eq "" "$(grep -l "$TRANSCRIPT_ONLY_PHRASE" "$QN_NOTES_DIR"/.recordings/*/summary.md 2>/dev/null)" "transcript-only phrase is in no summary"

# Dates spread over several months, so date-range filters have something to do.
months="$(ls "$QN_NOTES_DIR"/*.md | sed 's|.*/||; s|^\([0-9]*-[0-9]*\).*|\1|' | sort -u | wc -l | tr -d ' ')"
if [ "$months" -ge 4 ]; then pass "corpus spans $months months"
else fail "corpus spans several months" "got $months"; fi

# Same input, byte-identical output.
python3 "$HERE/fixtures.py" "$TMPROOT/det-a" >/dev/null 2>&1
python3 "$HERE/fixtures.py" "$TMPROOT/det-b" >/dev/null 2>&1
assert_exit 0 "fixtures.py is deterministic" diff -r "$TMPROOT/det-a" "$TMPROOT/det-b"

assert_exit 0 "fixtures.py honours --count" python3 "$HERE/fixtures.py" "$TMPROOT/count" --count 14
assert_eq "14" "$(ls "$TMPROOT/count"/*.md | wc -l | tr -d ' ')" "--count 14 writes 14 notes"

# --------------------------------------------------------------------------
section "synthetic audio"
# --------------------------------------------------------------------------
audio_dir="$QN_NOTES_DIR/.recordings/2026-08-20-1535-partner-intro"
if ! command -v say >/dev/null 2>&1; then
  skip "make_audio.sh" "say is missing"
elif ! command -v ffmpeg >/dev/null 2>&1; then
  skip "make_audio.sh" "ffmpeg is missing"
else
  assert_exit 0 "make_audio.sh writes two tracks" "$HERE/make_audio.sh" "$audio_dir" --seconds 8
  assert_file "$audio_dir/them.m4a" "them.m4a exists"
  assert_file "$audio_dir/me.m4a" "me.m4a exists"
  if command -v ffprobe >/dev/null 2>&1; then
    probe="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,sample_rate,channels -of default=nw=1 "$audio_dir/them.m4a")"
    assert_contains "$probe" "codec_name=aac" "them.m4a is AAC"
    assert_contains "$probe" "sample_rate=48000" "them.m4a is 48 kHz"
    assert_contains "$probe" "channels=2" "them.m4a is stereo"

    "$HERE/make_audio.sh" "$TMPROOT/silent" --seconds 6 --silent-me >/dev/null 2>&1
    vol="$(ffmpeg -nostdin -hide_banner -i "$TMPROOT/silent/me.m4a" -af volumedetect -f null - 2>&1 | sed -n 's/.*max_volume: \(-*[0-9]*\).*/\1/p')"
    if [ -n "$vol" ] && [ "$vol" -lt -60 ]; then pass "--silent-me makes a silent track ($vol dB)"
    else fail "--silent-me makes a silent track" "max_volume was [$vol] dB"; fi
    assert_file "$TMPROOT/silent/them.m4a" "--silent-me still writes them.m4a"

    "$HERE/make_audio.sh" "$TMPROOT/nothem" --seconds 6 --missing-them >/dev/null 2>&1
    if [ -e "$TMPROOT/nothem/them.m4a" ]; then fail "--missing-them writes no them.m4a" "the file is there"
    else pass "--missing-them writes no them.m4a"; fi
    assert_file "$TMPROOT/nothem/me.m4a" "--missing-them still writes me.m4a"
  else
    skip "audio format checks" "ffprobe is missing"
  fi
fi

# --------------------------------------------------------------------------
section "python unit tests"
# --------------------------------------------------------------------------
if [ -n "$(find "$ROOT/mcp" -name 'test_*.py' -print -quit 2>/dev/null)" ]; then
  if (cd "$ROOT" && python3 -m unittest discover -s mcp -p 'test_*.py'); then
    pass "python unittest discover"
  else
    fail "python unittest discover" "see the output above"
  fi
else
  skip "python unittest discover" "mcp/ has no test_*.py yet"
fi

# --------------------------------------------------------------------------
if ls "$ROOT"/test_*.py >/dev/null 2>&1; then
  if (cd "$ROOT" && python3 -m unittest discover -s . -p 'test_*.py' -t .); then
    pass "python unittest (health, merge)"
  else
    fail "python unittest (health, merge)" "see the output above"
  fi
else
  skip "python unittest (health, merge)" "no root test_*.py"
fi

# --------------------------------------------------------------------------
if ls "$HERE"/test_*.py >/dev/null 2>&1; then
  if (cd "$ROOT" && python3 -m unittest discover -s "$HERE" -p 'test_*.py' -t "$HERE"); then
    pass "python unittest (test/)"
  else
    fail "python unittest (test/)" "see the output above"
  fi
else
  fail "python unittest (test/)" "test/ has no test_*.py"
fi

section "watcher"
# --------------------------------------------------------------------------
if [ -x "$ROOT/build/watcher" ]; then
  assert_exit 0 "build/watcher --self-test" "$ROOT/build/watcher" --self-test
else
  skip "build/watcher --self-test" "build/watcher is not built"
fi

# --------------------------------------------------------------------------
section "qn CLI"
# --------------------------------------------------------------------------
guard_notes_dir

# A subcommand is only probed by reading the source. Running an unknown
# subcommand would fall through to the record path and start a real recording.
# The dispatch may match on the verb alone (`redo)`) or on the verb and the
# argument count (`redo:2)`). Both count as present.
qn_has() {
  grep -Eq "^[[:space:]]*\"?$1\"?(:[0-9]+)?\)" "$QN" && return 0
  grep -Eq "(=|\|)[[:space:]]*\"$1\"" "$QN"
}

# SPEC.md, "CLI surface". Every one of these must exist. A rename is a broken
# contract, so it fails here instead of quietly skipping the tests below.
for sub in redo play index pending approve vocab people prune doctor; do
  if qn_has "$sub"; then
    pass "qn has the $sub subcommand"
  else
    fail "qn has the $sub subcommand" "SPEC.md requires it; qn does not dispatch it"
  fi
done

# Stub the external tools so nothing calls whisper, ffmpeg or the real Claude.
STUB="$TMPROOT/stubbin"
mkdir -p "$STUB"
# QN_CLAUDE_LOG makes a call to claude visible. A test that expects no call
# checks the file is absent, so silence is proof and not an assumption.
cat > "$STUB/claude" <<'STUBEOF'
#!/bin/bash
if [ -n "${QN_CLAUDE_LOG:-}" ]; then printf 'claude called\n' >> "$QN_CLAUDE_LOG"; fi
# QN_CLAUDE_INPUT keeps the prompt, so a test can prove what was sent.
if [ -n "${QN_CLAUDE_INPUT:-}" ]; then cat > "$QN_CLAUDE_INPUT"; else cat >/dev/null; fi
cat <<'BODY'
## Summary
- A stubbed summary line.

## Decisions
- Ship behind a flag.

## My action items
- [ ] Write the migration script

## Their action items
- [ ] Priya: Confirm the storage quota

## Open questions
- Who owns the alert?
BODY
STUBEOF
cat > "$STUB/whisper-cli" <<'STUBEOF'
#!/bin/bash
of=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-of" ]; then of="$2"; fi
  shift
done
printf '{"transcription":[{"offsets":{"from":0,"to":2000},"text":" stub line"}]}\n' > "$of.json"
STUBEOF
cat > "$STUB/ffmpeg" <<'STUBEOF'
#!/bin/bash
out=""
for a in "$@"; do out="$a"; done
: > "$out"
STUBEOF
chmod +x "$STUB/claude" "$STUB/whisper-cli" "$STUB/ffmpeg"
: > "$TMPROOT/model.bin"

# 1. Missing dependencies must fail with a message that names the tool.
mkdir -p "$TMPROOT/emptybin"
capture 20 "$TMPROOT/nodeps.out" env PATH="$TMPROOT/emptybin" HOME="$HOME" \
  QN_NOTES_DIR="$QN_NOTES_DIR" QN_MODEL="$TMPROOT/missing-model.bin" /bin/bash "$QN"
if [ "$CAPTURE_CODE" -eq 0 ] || [ "$CAPTURE_CODE" -eq 124 ]; then
  fail "qn with no deps fails" "exit was $CAPTURE_CODE (124 means it hung)"
else
  pass "qn with no deps fails"
fi
assert_contains "$(cat "$TMPROOT/nodeps.out")" "ffmpeg" "the failure names the missing tool"

# 2. qn redo on a fixture recording writes a note with the SPEC shape.
if qn_has redo; then
  redo_dir="$QN_NOTES_DIR/.recordings/$FULL_ID"
  rm -f "$QN_NOTES_DIR/$FULL_ID.md"
  capture 60 "$TMPROOT/redo.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
    QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
    /bin/bash "$QN" redo "$redo_dir"
  assert_eq "0" "$CAPTURE_CODE" "qn redo exits 0"
  assert_file "$QN_NOTES_DIR/$FULL_ID.md" "qn redo writes the note"

  redo_text="$(cat "$QN_NOTES_DIR/$FULL_ID.md")"
  assert_eq "---" "$(head -1 "$QN_NOTES_DIR/$FULL_ID.md")" "qn redo opens the note with frontmatter"
  assert_contains "$redo_text" "title: \"roadmap review\"" "qn redo writes a quoted title"
  assert_contains "$redo_text" "date: 2026-03-04 09:30" "qn redo writes the date from the id"
  assert_contains "$redo_text" "attendees: [\"Priya\", \"Arjun\"]" "qn redo writes a quoted attendee list"
  assert_contains "$redo_text" "sharing: full" "qn redo writes sharing"
  assert_contains "$redo_text" "capture: " "qn redo writes capture"
  assert_contains "$redo_text" "warnings: [" "qn redo writes warnings"
  for heading in "Summary" "Decisions" "My action items" "Their action items" "Open questions"; do
    assert_contains "$redo_text" "## $heading" "qn redo writes the $heading section"
  done
  assert_contains "$redo_text" "## Transcript" "qn redo writes a transcript block"
  assert_contains "$redo_text" "[00:03] Them: " "qn redo keeps the transcript lines"
else
  fail "qn redo" "no redo subcommand in qn"
fi

# 3. qn play, under the QN_DRY_RUN convention.
if qn_has play; then
  capture 20 "$TMPROOT/play.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
    QN_DRY_RUN=1 /bin/bash "$QN" play "2026-08-20-1535-partner-intro" 00:05
  assert_eq "0" "$CAPTURE_CODE" "qn play exits 0 when the recording exists"
  assert_contains "$(cat "$TMPROOT/play.out")" "2026-08-20-1535-partner-intro" "qn play names the recording"
else
  fail "qn play" "no play subcommand in qn"
fi

# 4. qn index builds .index.db.
if qn_has index; then
  rm -f "$QN_NOTES_DIR/.index.db"
  capture 60 "$TMPROOT/index.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
    /bin/bash "$QN" index
  assert_eq "0" "$CAPTURE_CODE" "qn index exits 0"
  assert_file "$QN_NOTES_DIR/.index.db" "qn index creates .index.db"
else
  fail "qn index" "no index subcommand in qn"
fi

# 5. qn pending lists the meetings whose consent says local.
if qn_has pending; then
  capture 30 "$TMPROOT/pending.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
    /bin/bash "$QN" pending
  assert_eq "0" "$CAPTURE_CODE" "qn pending exits 0"
  assert_contains "$(cat "$TMPROOT/pending.out")" "$LOCAL_ID" "qn pending lists the meeting whose consent says local"
  assert_missing "$(cat "$TMPROOT/pending.out")" "$FULL_ID" "qn pending leaves out a meeting whose consent says full"
else
  fail "qn pending" "no pending subcommand in qn"
fi

# 6. qn people builds the roster, and what you write in it reaches Claude.
if qn_has people; then
  capture 60 "$TMPROOT/people.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
    /bin/bash "$QN" people
  assert_eq "0" "$CAPTURE_CODE" "qn people exits 0"
  assert_file "$QN_NOTES_DIR/people.md" "qn people writes the roster"
  assert_contains "$(cat "$QN_NOTES_DIR/people.md")" "Priya" "the roster lists an attendee"

  # Write a note against Priya, the way a user would, then re-run the meeting
  # she attended. The prompt must carry what was written.
  python3 - "$QN_NOTES_DIR/people.md" <<'PYEOF'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
out = []
for line in text.splitlines(True):
    if line.startswith("- **Priya**"):
        line = line.rstrip("\n") + " \u2014 my manager, owns billing\n"
    out.append(line)
open(path, "w", encoding="utf-8").write("".join(out))
PYEOF
  assert_contains "$(cat "$QN_NOTES_DIR/people.md")" "my manager" "the roster keeps what you wrote"

  PROMPT_FILE="$TMPROOT/claude-prompt.txt"
  rm -f "$PROMPT_FILE"
  capture 60 "$TMPROOT/people-redo.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
    QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
    QN_CLAUDE_INPUT="$PROMPT_FILE" \
    /bin/bash "$QN" redo "$QN_NOTES_DIR/.recordings/$FULL_ID"
  assert_eq "0" "$CAPTURE_CODE" "qn redo exits 0 with a roster in place"
  assert_file "$PROMPT_FILE" "the prompt reached the claude stub"
  assert_contains "$(cat "$PROMPT_FILE")" "PEOPLE IN THIS MEETING" "the prompt has the people block"
  assert_contains "$(cat "$PROMPT_FILE")" "my manager, owns billing" "what you wrote about Priya reaches Claude"
  assert_contains "$(cat "$PROMPT_FILE")" "Arjun" "an attendee with no roster note is still listed"

  # The roster must survive its own refresh. A note you wrote is not a fixture.
  capture 60 "$TMPROOT/people2.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
    /bin/bash "$QN" people
  assert_contains "$(cat "$QN_NOTES_DIR/people.md")" "my manager" "your note survives the next refresh"
else
  fail "qn people" "no people subcommand in qn"
fi

# 7. qn prune deletes old audio, and nothing else.
if qn_has prune; then
  PRUNE_ROOT="$TMPROOT/prunable"
  mkdir -p "$PRUNE_ROOT/.recordings"

  # One old and shareable, one old but still held, one shareable but new.
  for spec in "old-full full" "old-held local" "new-full full"; do
    pid="$(printf '%s' "$spec" | cut -d' ' -f1)"
    verdict="$(printf '%s' "$spec" | cut -d' ' -f2)"
    work="$PRUNE_ROOT/.recordings/2026-01-01-1000-$pid"
    mkdir -p "$work"
    printf '%s\n' "$verdict" > "$work/consent"
    printf 'audio' > "$work/them.m4a"
    printf 'audio' > "$work/me.m4a"
    printf '[00:00] Me: keep me\n' > "$work/transcript.txt"
    case "$pid" in
      old-*) touch -t 202001010000 "$work/them.m4a" "$work/me.m4a" ;;
    esac
  done

  # A dry run must delete nothing at all.
  capture 30 "$TMPROOT/prune-dry.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$PRUNE_ROOT" \
    QN_DRY_RUN=1 /bin/bash "$QN" prune
  assert_eq "0" "$CAPTURE_CODE" "qn prune --dry-run exits 0"
  assert_contains "$(cat "$TMPROOT/prune-dry.out")" "would free" "a dry run says it only would free space"
  assert_file "$PRUNE_ROOT/.recordings/2026-01-01-1000-old-full/them.m4a" "a dry run deletes nothing"

  capture 30 "$TMPROOT/prune.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$PRUNE_ROOT" \
    /bin/bash "$QN" prune
  assert_eq "0" "$CAPTURE_CODE" "qn prune exits 0"

  if [ -f "$PRUNE_ROOT/.recordings/2026-01-01-1000-old-full/them.m4a" ]; then
    fail "qn prune deletes old audio" "them.m4a is still there"
  else
    pass "qn prune deletes old audio"
  fi
  assert_file "$PRUNE_ROOT/.recordings/2026-01-01-1000-old-full/transcript.txt" \
    "qn prune keeps the transcript"
  assert_file "$PRUNE_ROOT/.recordings/2026-01-01-1000-old-full/consent" \
    "qn prune keeps the consent record"
  assert_file "$PRUNE_ROOT/.recordings/2026-01-01-1000-old-held/them.m4a" \
    "qn prune leaves a meeting still awaiting approval"
  assert_file "$PRUNE_ROOT/.recordings/2026-01-01-1000-new-full/them.m4a" \
    "qn prune leaves audio newer than the cutoff"

  # --older-than 0 makes everything old, but the held one is still protected.
  capture 30 "$TMPROOT/prune0.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$PRUNE_ROOT" \
    /bin/bash "$QN" --older-than 0d prune
  assert_file "$PRUNE_ROOT/.recordings/2026-01-01-1000-old-held/them.m4a" \
    "a held meeting survives --older-than 0d"

  capture 30 "$TMPROOT/prune-bad.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$PRUNE_ROOT" \
    /bin/bash "$QN" --older-than lots prune
  if [ "$CAPTURE_CODE" -eq 0 ]; then
    fail "--older-than rejects a non-number" "it exited 0"
  else
    pass "--older-than rejects a non-number"
  fi

  # A redo on pruned audio must explain itself, not fail inside whisper.
  capture 30 "$TMPROOT/prune-redo.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$PRUNE_ROOT" \
    QN_MODEL="$TMPROOT/model.bin" \
    /bin/bash "$QN" redo "2026-01-01-1000-old-full"
  assert_contains "$(cat "$TMPROOT/prune-redo.out")" "pruned" "qn redo on pruned audio says the audio was pruned"
else
  fail "qn prune" "no prune subcommand in qn"
fi

# --------------------------------------------------------------------------
section "auto_prune"
# --------------------------------------------------------------------------
# Housekeeping after a recording, on the same code path as `qn prune`, at most
# once a day. Off unless the settings file asks for it.
guard_notes_dir

# An old recording that nothing is going to touch directly.
ANCIENT="$QN_NOTES_DIR/.recordings/2020-01-01-1000-ancient"
make_ancient() {
  rm -rf "$ANCIENT"
  mkdir -p "$ANCIENT"
  printf 'full\n' > "$ANCIENT/consent"
  printf 'audio' > "$ANCIENT/them.m4a"
  printf 'audio' > "$ANCIENT/me.m4a"
  touch -t 202001010000 "$ANCIENT/them.m4a" "$ANCIENT/me.m4a"
  rm -f "$QN_NOTES_DIR/.recordings/.last-prune"
}

redo_one() { # redo_one <config-file> <outfile>
  capture 60 "$2" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" QN_CONFIG="$1" \
    QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
    /bin/bash "$QN" redo "$QN_NOTES_DIR/.recordings/$FULL_ID"
}

printf 'auto_prune = no\n' > "$TMPROOT/auto-off"
printf 'auto_prune = yes\nprune_days = 30\n' > "$TMPROOT/auto-on"

# Off by default: nobody's audio disappears because they pulled a new version.
make_ancient
redo_one "$TMPROOT/auto-off" "$TMPROOT/auto-off.out"
assert_eq "0" "$CAPTURE_CODE" "a recording still works with auto_prune off"
assert_file "$ANCIENT/them.m4a" "auto_prune off leaves old audio alone"

# On: the old recording loses its audio, and keeps everything else.
make_ancient
redo_one "$TMPROOT/auto-on" "$TMPROOT/auto-on.out"
assert_eq "0" "$CAPTURE_CODE" "a recording still works with auto_prune on"
if [ -f "$ANCIENT/them.m4a" ]; then
  fail "auto_prune on deletes old audio" "them.m4a is still there"
else
  pass "auto_prune on deletes old audio"
fi
assert_file "$ANCIENT/consent" "auto_prune keeps everything that is not audio"
assert_file "$ANCIENT/.pruned" "auto_prune leaves the marker qn redo reads"
assert_contains "$(cat "$TMPROOT/auto-on.out")" "2020-01-01-1000-ancient" "an automatic prune names what it deleted"
assert_file "$QN_NOTES_DIR/$FULL_ID.md" "the note is still written when auto_prune runs"

# Once a day. The stamp is already today's, so a second recording must not scan.
make_ancient
printf '%s\n' "$(date '+%Y-%m-%d')" > "$QN_NOTES_DIR/.recordings/.last-prune"
redo_one "$TMPROOT/auto-on" "$TMPROOT/auto-twice.out"
assert_file "$ANCIENT/them.m4a" "auto_prune runs at most once a day"

# A stamp from another day must let it run again.
printf '2019-01-01\n' > "$QN_NOTES_DIR/.recordings/.last-prune"
redo_one "$TMPROOT/auto-on" "$TMPROOT/auto-yesterday.out"
if [ -f "$ANCIENT/them.m4a" ]; then
  fail "auto_prune runs again the next day" "them.m4a is still there"
else
  pass "auto_prune runs again the next day"
fi
assert_eq "$(date '+%Y-%m-%d')" "$(cat "$QN_NOTES_DIR/.recordings/.last-prune")" "auto_prune stamps the day it ran"

# A meeting awaiting approval is protected by the same code `qn prune` uses.
make_ancient
printf 'local\n' > "$ANCIENT/consent"
rm -f "$ANCIENT/.pruned"
redo_one "$TMPROOT/auto-on" "$TMPROOT/auto-held.out"
assert_file "$ANCIENT/them.m4a" "auto_prune never touches a meeting awaiting approval"
rm -rf "$ANCIENT"

# A value that is neither yes nor no must fail, naming the setting.
printf 'auto_prune = maybe\n' > "$TMPROOT/auto-bad"
capture 20 "$TMPROOT/auto-bad.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
  QN_CONFIG="$TMPROOT/auto-bad" /bin/bash "$QN" pending
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "a bad auto_prune is rejected" "it exited 0"
else
  pass "a bad auto_prune is rejected"
fi
assert_contains "$(cat "$TMPROOT/auto-bad.out")" "auto_prune" "the failure names the bad setting"

# --------------------------------------------------------------------------
section "installed on the PATH, and the settings file"
# --------------------------------------------------------------------------
# `make install` symlinks qn into a bin directory. BASH_SOURCE names the
# symlink, not its target, so a qn that does not resolve it looks for its own
# models and helpers inside that bin directory and fails everywhere.
FAKEBIN="$TMPROOT/fakebin"
mkdir -p "$FAKEBIN"
ln -sf "$QN" "$FAKEBIN/qn"

capture 30 "$TMPROOT/linked.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
  /bin/bash "$FAKEBIN/qn" doctor
assert_contains "$(cat "$TMPROOT/linked.out")" "$ROOT" "a symlinked qn still finds its own repo"
assert_missing "$(cat "$TMPROOT/linked.out")" "$FAKEBIN/models" "a symlinked qn does not look inside the bin directory"

# A symlink to a symlink, which is what a second `make install` can leave.
ln -sf "$FAKEBIN/qn" "$FAKEBIN/qn2"
capture 30 "$TMPROOT/linked2.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
  /bin/bash "$FAKEBIN/qn2" doctor
assert_contains "$(cat "$TMPROOT/linked2.out")" "$ROOT" "a chain of symlinks still resolves"

# The settings file. The MCP server never sees your shell, so this file is the
# only way a moved notes folder reaches Claude.
CONF_DIR="$TMPROOT/conf"
CONF="$CONF_DIR/config"
CONF_NOTES="$TMPROOT/conf-notes"
mkdir -p "$CONF_DIR" "$CONF_NOTES"
printf '# my settings\nnotes = %s\nprune_days = 7\nlanguage = fr\n' "$CONF_NOTES" > "$CONF"

# -u QN_NOTES_DIR matters: this suite exports it, and the environment is meant
# to beat the file. Without unsetting it this would test the wrong thing.
capture 30 "$TMPROOT/conf.out" env -u QN_NOTES_DIR PATH="$STUB:$PATH" QN_CONFIG="$CONF" \
  /bin/bash "$QN" doctor
assert_contains "$(cat "$TMPROOT/conf.out")" "$CONF_NOTES" "the settings file moves the notes folder"
assert_contains "$(cat "$TMPROOT/conf.out")" "$CONF" "doctor names the settings file it read"

# The same file, read by the code the MCP server actually uses.
mcp_notes="$(env -u QN_NOTES_DIR QN_CONFIG="$CONF" QN_ROOT="$ROOT" python3 -c '
import os, sys
sys.path.insert(0, os.path.join(os.environ["QN_ROOT"], "mcp"))
import index
print(index.notes_dir())' 2>/dev/null || true)"
# TMPDIR ends in a slash, so $CONF_NOTES holds a doubled one. Python's
# abspath collapses it, and `cd && pwd` collapses it the same way.
assert_eq "$(cd "$CONF_NOTES" && pwd)" "$mcp_notes" "the MCP server reads the same settings file"

# Precedence: the environment always beats the file.
capture 30 "$TMPROOT/conf-env.out" env PATH="$STUB:$PATH" QN_CONFIG="$CONF" \
  QN_NOTES_DIR="$QN_NOTES_DIR" /bin/bash "$QN" doctor
assert_contains "$(cat "$TMPROOT/conf-env.out")" "QN_NOTES_DIR" "doctor says the environment variable won"
assert_missing "$(cat "$TMPROOT/conf-env.out")" "notes:    $CONF_NOTES" "the environment beats the settings file"

# A bad number must fail here, naming the setting, not deep inside find.
printf 'prune_days = lots\n' > "$TMPROOT/bad-config"
capture 20 "$TMPROOT/conf-bad.out" env PATH="$STUB:$PATH" QN_CONFIG="$TMPROOT/bad-config" \
  QN_NOTES_DIR="$QN_NOTES_DIR" /bin/bash "$QN" prune
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "a bad prune_days is rejected" "it exited 0"
else
  pass "a bad prune_days is rejected"
fi
assert_contains "$(cat "$TMPROOT/conf-bad.out")" "prune_days" "the failure names the bad setting"

# --------------------------------------------------------------------------
section "consent, end to end"
# --------------------------------------------------------------------------
# Everything below feeds a note that `qn` itself wrote to the parser that the
# MCP server itself uses. Nothing here reads a fixture file, so a change to
# either side that breaks the other shows up as a failure.
guard_notes_dir

RT_DIR="$TMPROOT/roundtrip"
CLAUDE_LOG="$TMPROOT/claude-calls.log"
python3 "$HERE/fixtures.py" "$RT_DIR" >/dev/null 2>&1

# What the real parser makes of a note file. It returns None for anything it
# refuses to index.
parse_note_repr() {
  python3 -c "import sys; sys.path.insert(0, '$ROOT/mcp'); import index; print(index.parse_note(sys.argv[1]))" "$1"
}

# One field, as the real parser reads it back. EXCLUDED means the parser
# refused the note, which is the right answer for sharing: local.
parsed_field() { # parsed_field <note> <title|attendees|sharing>
  python3 - "$1" "$2" <<PYEOF
import sys
sys.path.insert(0, "$ROOT/mcp")
import index
note = index.parse_note(sys.argv[1])
if note is None:
    print("EXCLUDED")
elif sys.argv[2] == "attendees":
    print(", ".join(note["attendees"]))
else:
    print(note[sys.argv[2]])
PYEOF
}

# One frontmatter field, straight from the real frontmatter reader. This is
# how a held note is checked, because the parser above will not return it.
frontmatter_field() { # frontmatter_field <note> <key>
  python3 - "$1" "$2" <<PYEOF
import sys
sys.path.insert(0, "$ROOT/mcp")
import index
with open(sys.argv[1], encoding="utf-8") as handle:
    fields, _ = index.parse_frontmatter(handle.read())
value = fields.get(sys.argv[2], "")
print(", ".join(value) if isinstance(value, list) else value)
PYEOF
}

# Ids in an index file, space separated.
db_ids() {
  python3 - "$1" <<'PYEOF'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
print(" ".join(row[0] for row in connection.execute("SELECT id FROM meetings ORDER BY id")))
PYEOF
}

# Rebuild one recording's note with a given consent, under the stubs.
redo_with_consent() { # redo_with_consent <notes-dir> <id> <consent> <outfile>
  local dir="$1" id="$2" consent="$3" out="$4"
  local work="$dir/.recordings/$id"
  printf '%s\n' "$consent" > "$work/consent"
  rm -f "$dir/$id.md" "$work/summary.md" "$CLAUDE_LOG"
  capture 60 "$out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$dir" \
    QN_CLAUDE_LOG="$CLAUDE_LOG" QN_MODEL="$TMPROOT/model.bin" \
    QN_VAD_MODEL="$TMPROOT/no-vad.bin" /bin/bash "$QN" redo "$work"
}

if qn_has redo; then
  rt_note="$RT_DIR/$FULL_ID.md"

  # --- consent: full. Claude is called, and the note is indexable. ---------
  redo_with_consent "$RT_DIR" "$FULL_ID" full "$TMPROOT/rt-full.out"
  assert_eq "0" "$CAPTURE_CODE" "qn redo exits 0 when consent says full"
  assert_file "$rt_note" "qn redo writes a real note when consent says full"
  assert_file "$CLAUDE_LOG" "consent full calls the claude stub"
  assert_contains "$(cat "$rt_note")" "sharing: full" "the note qn wrote says sharing: full"
  assert_contains "$(parse_note_repr "$rt_note")" "'sharing': 'full'" \
    "the real parser accepts the note qn wrote"
  assert_eq "roadmap review" "$(parsed_field "$rt_note" title)" \
    "the parser reads back the title qn wrote"
  assert_eq "Priya, Arjun" "$(parsed_field "$rt_note" attendees)" \
    "the parser reads back the attendees qn wrote"
  assert_eq "full" "$(parsed_field "$rt_note" sharing)" \
    "the parser reads back sharing: full"

  # --- consent: local. Claude is never called, and the note stays out. -----
  redo_with_consent "$RT_DIR" "$FULL_ID" local "$TMPROOT/rt-local.out"
  assert_eq "0" "$CAPTURE_CODE" "qn redo exits 0 when consent says local"
  assert_file "$rt_note" "qn redo writes a real note when consent says local"
  if [ -e "$CLAUDE_LOG" ]; then
    fail "consent local never calls the claude stub" "the stub logged: $(cat "$CLAUDE_LOG")"
  else
    pass "consent local never calls the claude stub"
  fi
  rt_local_text="$(cat "$rt_note")"
  assert_contains "$rt_local_text" "sharing: local" "the note qn wrote says sharing: local"
  assert_missing "$rt_local_text" "## Summary" "a held note carries no AI sections"
  assert_contains "$rt_local_text" "## Transcript" "a held note keeps its transcript"
  assert_eq "EXCLUDED" "$(parsed_field "$rt_note" sharing)" \
    "the real parser refuses to index the held note"
  assert_eq "None" "$(parse_note_repr "$rt_note")" "parse_note returns None for the held note"
  assert_eq "local" "$(frontmatter_field "$rt_note" sharing)" \
    "the frontmatter reader reads back sharing: local"
  assert_eq "roadmap review" "$(frontmatter_field "$rt_note" title)" \
    "the frontmatter reader reads back the title qn wrote"
  assert_eq "Priya, Arjun" "$(frontmatter_field "$rt_note" attendees)" \
    "the frontmatter reader reads back the attendees qn wrote"

  # The index over that same directory must hold the full note and nothing
  # else this recording produced.
  capture 60 "$TMPROOT/rt-index.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$RT_DIR" \
    /bin/bash "$QN" index
  assert_eq "0" "$CAPTURE_CODE" "qn index exits 0 over the round-trip corpus"
  assert_missing " $(db_ids "$RT_DIR/.index.db") " " $FULL_ID " \
    "the held note qn wrote is absent from .index.db"
else
  fail "qn redo round-trip" "no redo subcommand in qn"
fi

# The fixture corpus: every LOCAL id must be missing from the index that
# `qn index` built above, and the full ids must be there.
indexed_ids=" $(db_ids "$QN_NOTES_DIR/.index.db") "
LOCAL_IDS="$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import fixtures; print(' '.join(fixtures.LOCAL_IDS))" "$HERE")"
for held in $LOCAL_IDS; do
  assert_missing "$indexed_ids" " $held " "sharing: local keeps $held out of .index.db"
done
assert_contains "$indexed_ids" " $FULL_ID " "a shareable meeting is in .index.db"
assert_contains "$indexed_ids" " $NOTES_ONLY_ID " "another shareable meeting is in .index.db"

# 6. Slug rule: "SDK Sync!!" becomes sdk-sync. The rule is read out of qn
#    itself, because running the record path would start a real recording.
slug_fn="$(sed -n '/^[[:space:]]*slugify()[[:space:]]*{/,/^[[:space:]]*}/p' "$QN")"
slug_src="$(grep -E '^[[:space:]]*slug=\$\(printf' "$QN" | head -1)"
if [ -n "$slug_fn" ]; then
  assert_eq "sdk-sync" "$(eval "$slug_fn"; slugify "SDK Sync!!")" "slug: \"SDK Sync!!\" -> sdk-sync"
  assert_eq "q3-planning-2026" "$(eval "$slug_fn"; slugify "  Q3 Planning / 2026  ")" "slug: punctuation collapses to one dash"
  assert_eq "" "$(eval "$slug_fn"; slugify "!!!")" "slug: an empty result is left empty for qn to default"
elif [ -n "$slug_src" ]; then
  assert_eq "sdk-sync" "$(title="SDK Sync!!"; eval "$slug_src"; printf '%s' "${slug:-}")" \
    "slug: \"SDK Sync!!\" -> sdk-sync"
else
  skip "slug rule" "no slugify function or slug= line found in qn"
fi

# --------------------------------------------------------------------------
printf '\n%s%d passed%s  %s%d failed%s  %s%d skipped%s\n' \
  "$G" "$PASSED" "$N" "$R" "$FAILED" "$N" "$Y" "$SKIPPED" "$N"
if [ "$FAILED" -gt 0 ]; then exit 1; fi
exit 0
