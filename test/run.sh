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
  if ! python3 "$ROOT/lib/merge.py" "$work" | diff -q - "$work/transcript.txt" >/dev/null 2>&1; then
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
# Every test_*.py lives in test/, so one call finds all of them. This used to
# be three calls over three directories, and `unittest discover` does not
# recurse into a directory that is not a package. A file in a fourth place ran
# nowhere, and said nothing about it.
if (cd "$ROOT" && python3 -m unittest discover -s "$HERE" -p 'test_*.py' -t "$HERE"); then
  pass "python unittest"
else
  fail "python unittest" "see the output above"
fi

# A module with no test file beside it is a hole in the suite, so name it here
# rather than let the count quietly stay the same.
for module in "$ROOT"/lib/*.py "$ROOT"/mcp/*.py; do
  base="$(basename "$module" .py)"
  case "$base" in
    __*) continue ;;
  esac
  if [ -f "$HERE/test_$base.py" ]; then
    pass "$base has tests"
  else
    fail "$base has tests" "no test/test_$base.py"
  fi
done

section "recorder"
# --------------------------------------------------------------------------
# The self-test ends by signalling itself. A recorder that dies on a signal
# instead of stopping loses the whole meeting, because the index is written
# last, so those checks exit non-zero here.
if [ -x "$ROOT/build/recorder" ]; then
  assert_exit 0 "build/recorder --self-test" "$ROOT/build/recorder" --self-test
else
  skip "build/recorder --self-test" "build/recorder is not built"
fi

# --------------------------------------------------------------------------
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
for sub in redo play index pending approve vocab people prune confirm skip doctor; do
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
# QN_WHISPER_LOG makes a call visible, so a test that expects no call can
# prove it, exactly the way QN_CLAUDE_LOG does for claude.
if [ -n "${QN_WHISPER_LOG:-}" ]; then printf 'whisper called\n' >> "$QN_WHISPER_LOG"; fi
of=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-of" ]; then of="$2"; fi
  shift
done
printf '{"transcription":[{"offsets":{"from":0,"to":2000},"text":" stub line"}]}\n' > "$of.json"
STUBEOF
cat > "$STUB/ffmpeg" <<'STUBEOF'
#!/bin/bash
# The last argument is the output file, except when it is `-`, which means
# stdout. health.py measures loudness and discards the audio with `-f null -`,
# so taking that literally created a file named `-` in the repo, once per run.
out=""
for a in "$@"; do out="$a"; done
if [ "$out" = "-" ]; then exit 0; fi
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

  # What is waiting is what has no notes, not what you answered. A late "Full
  # notes" click leaves a meeting with no notes and a consent that says full.
  # Reading the consent alone hid it from this list and from everything else.
  PEND_DIR="$TMPROOT/pending-cases"
  mkdir -p "$PEND_DIR/.recordings"
  for case in "answered-late full no" "properly-sent full yes" "held-normally local no" "recording-now full no"; do
    pid="$(printf '%s' "$case" | cut -d' ' -f1)"
    verdict="$(printf '%s' "$case" | cut -d' ' -f2)"
    has_notes="$(printf '%s' "$case" | cut -d' ' -f3)"
    pend_work="$PEND_DIR/.recordings/2026-09-09-0900-$pid"
    mkdir -p "$pend_work"
    printf '%s\n' "$verdict" > "$pend_work/consent"
    if [ "$has_notes" = "yes" ]; then printf '## Summary\n- notes\n' > "$pend_work/summary.md"; fi
  done
  # The meeting being recorded right now is not waiting for anything.
  printf '%s' "$PEND_DIR/.recordings/2026-09-09-0900-recording-now" > "$PEND_DIR/.recordings/.recording"

  capture 30 "$TMPROOT/pending2.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$PEND_DIR" \
    /bin/bash "$QN" pending
  pending2="$(cat "$TMPROOT/pending2.out")"
  assert_eq "0" "$CAPTURE_CODE" "qn pending exits 0 over the edge cases"
  assert_contains "$pending2" "answered-late" "a late full answer with no notes is listed"
  assert_contains "$pending2" "never sent" "and it says why it is listed"
  assert_contains "$pending2" "held-normally" "a held meeting is still listed"
  assert_missing "$pending2" "properly-sent" "a meeting that has notes is not listed"
  assert_missing "$pending2" "recording-now" "the recording in progress is not listed"
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
    /bin/bash "$QN" prune --older-than 0d
  assert_file "$PRUNE_ROOT/.recordings/2026-01-01-1000-old-held/them.m4a" \
    "a held meeting survives --older-than 0d"

  capture 30 "$TMPROOT/prune-bad.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$PRUNE_ROOT" \
    /bin/bash "$QN" prune --older-than lots
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
section "rebuilding the notes without listening again"
# --------------------------------------------------------------------------
# Editing prompt.md changes how the notes read. It does not change what anyone
# said, so --notes-only reuses the words whisper already worked out.
guard_notes_dir

REUSE_DIR="$TMPROOT/notes-only"
REUSE_ID="2026-06-06-0900-prompt-edit"
REUSE_WORK="$REUSE_DIR/.recordings/$REUSE_ID"
WHISPER_LOG="$TMPROOT/whisper-calls.log"
mkdir -p "$REUSE_WORK"
printf 'full\n' > "$REUSE_WORK/consent"
printf 'Priya\n' > "$REUSE_WORK/attendees.txt"
printf 'audio' > "$REUSE_WORK/them.m4a"
printf 'audio' > "$REUSE_WORK/me.m4a"
cat > "$REUSE_WORK/them.json" <<'JSONEOF'
{"transcription":[{"offsets":{"from":1000,"to":4000},"text":" the release ships on friday"}]}
JSONEOF
cat > "$REUSE_WORK/me.json" <<'JSONEOF'
{"transcription":[{"offsets":{"from":5000,"to":7000},"text":" I will write the migration"}]}
JSONEOF

reuse_qn() { # reuse_qn <outfile> <args...>
  local out="$1"; shift
  rm -f "$WHISPER_LOG"
  capture 60 "$out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$REUSE_DIR" \
    QN_WHISPER_LOG="$WHISPER_LOG" QN_CLAUDE_LOG="$TMPROOT/reuse-claude.log" \
    QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
    /bin/bash "$QN" "$@"
}

# --notes-only must not listen again, and must keep the real words. This runs
# before the plain redo, whose whisper stub would overwrite them.
rm -f "$TMPROOT/reuse-claude.log"
reuse_qn "$TMPROOT/reuse.out" --notes-only redo "$REUSE_ID"
assert_eq "0" "$CAPTURE_CODE" "qn --notes-only redo exits 0"
if [ -e "$WHISPER_LOG" ]; then
  fail "--notes-only never runs whisper" "the stub logged: $(cat "$WHISPER_LOG")"
else
  pass "--notes-only never runs whisper"
fi
assert_file "$TMPROOT/reuse-claude.log" "--notes-only still rebuilds the notes with claude"
assert_file "$REUSE_DIR/$REUSE_ID.md" "--notes-only writes the note"
reuse_text="$(cat "$REUSE_DIR/$REUSE_ID.md")"
assert_contains "$reuse_text" "the release ships on friday" "--notes-only keeps what them.json said"
assert_contains "$reuse_text" "I will write the migration" "--notes-only keeps what me.json said"
assert_contains "$reuse_text" "sharing: full" "--notes-only writes a complete note"
assert_contains "$(cat "$TMPROOT/reuse.out")" "reusing the words" "--notes-only says what it is doing"

# A plain redo still listens to the audio again.
reuse_qn "$TMPROOT/reuse-plain.out" redo "$REUSE_ID"
assert_eq "0" "$CAPTURE_CODE" "a plain qn redo still exits 0"
assert_file "$WHISPER_LOG" "a plain qn redo does run whisper"

# Audio but no words: refuse, rather than write a note in which nobody spoke.
BARE_ID="2026-06-06-1000-never-transcribed"
BARE_WORK="$REUSE_DIR/.recordings/$BARE_ID"
mkdir -p "$BARE_WORK"
printf 'full\n' > "$BARE_WORK/consent"
printf 'audio' > "$BARE_WORK/them.m4a"
printf 'audio' > "$BARE_WORK/me.m4a"
reuse_qn "$TMPROOT/reuse-bare.out" --notes-only redo "$BARE_ID"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "--notes-only refuses a recording with no words" "it exited 0"
else
  pass "--notes-only refuses a recording with no words"
fi
assert_contains "$(cat "$TMPROOT/reuse-bare.out")" "qn redo $BARE_ID" "the refusal names the command that fixes it"
if [ -e "$REUSE_DIR/$BARE_ID.md" ]; then
  fail "--notes-only writes no note when it refuses" "the note is there"
else
  pass "--notes-only writes no note when it refuses"
fi

# One track transcribed and one not must also be refused: half a conversation
# reads like a complete one.
printf 'audio' > "$BARE_WORK/them.m4a"
cat > "$BARE_WORK/them.json" <<'JSONEOF'
{"transcription":[{"offsets":{"from":0,"to":2000},"text":" only one side"}]}
JSONEOF
reuse_qn "$TMPROOT/reuse-half.out" --notes-only redo "$BARE_ID"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "--notes-only refuses a half-transcribed recording" "it exited 0"
else
  pass "--notes-only refuses a half-transcribed recording"
fi
assert_contains "$(cat "$TMPROOT/reuse-half.out")" "me track" "the refusal names the track that is missing"

# --------------------------------------------------------------------------
section "approving a meeting whose audio was pruned"
# --------------------------------------------------------------------------
# Pruning deletes the audio and keeps the words. Approval needs the words, so
# a pruned meeting can still be sent, and must not be measured again: the
# health check would report both tracks missing and call it broken.
guard_notes_dir

GONE_DIR="$TMPROOT/audio-gone"
GONE_ID="2026-02-02-1400-held-then-pruned"
GONE_WORK="$GONE_DIR/.recordings/$GONE_ID"
mkdir -p "$GONE_WORK"
printf 'local\n' > "$GONE_WORK/consent"
printf 'Priya\n' > "$GONE_WORK/attendees.txt"
cat > "$GONE_WORK/them.json" <<'JSONEOF'
{"transcription":[{"offsets":{"from":1000,"to":4000},"text":" we agreed on the storage quota"}]}
JSONEOF
cat > "$GONE_WORK/me.json" <<'JSONEOF'
{"transcription":[{"offsets":{"from":5000,"to":7000},"text":" I will raise the ticket"}]}
JSONEOF
date '+%Y-%m-%d' > "$GONE_WORK/.pruned"
python3 "$ROOT/lib/merge.py" "$GONE_WORK" > "$GONE_WORK/transcript.txt"

# The note as it stood while it was held, with a capture verdict worth keeping.
cat > "$GONE_DIR/$GONE_ID.md" <<'NOTEEOF'
---
title: "held then pruned"
date: 2026-02-02 14:00
attendees: ["Priya"]
sharing: local
capture: ok
warnings: []
---

# held then pruned

_Kept on this Mac. Claude has not seen this meeting._
NOTEEOF

gone_qn() { # gone_qn <outfile> <args...>
  local out="$1"; shift
  rm -f "$WHISPER_LOG" "$TMPROOT/gone-claude.log"
  capture 60 "$out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$GONE_DIR" \
    QN_WHISPER_LOG="$WHISPER_LOG" QN_CLAUDE_LOG="$TMPROOT/gone-claude.log" \
    QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
    /bin/bash "$QN" "$@"
}

gone_qn "$TMPROOT/gone-approve.out" approve "$GONE_ID"
assert_eq "0" "$CAPTURE_CODE" "qn approve works when the audio was pruned"
assert_contains "$(cat "$TMPROOT/gone-approve.out")" "pruned" "approve says the audio is gone"
assert_file "$TMPROOT/gone-claude.log" "approve still sends the meeting to claude"
if [ -e "$WHISPER_LOG" ]; then
  fail "approve never runs whisper on pruned audio" "the stub logged: $(cat "$WHISPER_LOG")"
else
  pass "approve never runs whisper on pruned audio"
fi

gone_note="$(cat "$GONE_DIR/$GONE_ID.md")"
assert_contains "$gone_note" "sharing: full" "the approved note is shareable"
assert_contains "$gone_note" "we agreed on the storage quota" "the words survive the missing audio"
assert_contains "$gone_note" "capture: ok" "the capture verdict is kept, not measured again"
assert_contains "$gone_note" "warnings: []" "the warnings are kept, not measured again"
assert_missing "$gone_note" "track is missing" "a pruned recording is never called broken"

# A pruned recording that was never transcribed has nothing left to rebuild.
EMPTY_ID="2026-02-02-1500-nothing-left"
EMPTY_WORK="$GONE_DIR/.recordings/$EMPTY_ID"
mkdir -p "$EMPTY_WORK"
printf 'local\n' > "$EMPTY_WORK/consent"
date '+%Y-%m-%d' > "$EMPTY_WORK/.pruned"
gone_qn "$TMPROOT/gone-empty.out" approve "$EMPTY_ID"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "a pruned recording with no words is refused" "it exited 0"
else
  pass "a pruned recording with no words is refused"
fi
assert_contains "$(cat "$TMPROOT/gone-empty.out")" "never transcribed" "the refusal says why nothing can be done"

# --------------------------------------------------------------------------
section "prune asks before it deletes held audio"
# --------------------------------------------------------------------------
# A meeting awaiting approval can now be pruned, because approve rebuilds it
# from the words. That is still the user's call, so prune asks first, and a
# silent run always answers no.
guard_notes_dir

HELD_ROOT="$TMPROOT/held-prune"

make_held_corpus() {
  rm -rf "$HELD_ROOT"
  mkdir -p "$HELD_ROOT/.recordings"
  # id consent has-words
  for spec in "old-held local yes" "old-held-2 local yes" "no-words local no" "old-full full yes"; do
    local pid verdict words work
    pid="$(printf '%s' "$spec" | cut -d' ' -f1)"
    verdict="$(printf '%s' "$spec" | cut -d' ' -f2)"
    words="$(printf '%s' "$spec" | cut -d' ' -f3)"
    work="$HELD_ROOT/.recordings/2026-01-01-1000-$pid"
    mkdir -p "$work"
    printf '%s\n' "$verdict" > "$work/consent"
    printf 'audio' > "$work/them.m4a"
    printf 'audio' > "$work/me.m4a"
    if [ "$words" = "yes" ]; then
      printf '{"transcription":[{"offsets":{"from":0,"to":2000},"text":" a line"}]}\n' > "$work/them.json"
    fi
    touch -t 202001010000 "$work/them.m4a" "$work/me.m4a"
  done
}

held_audio() { # held_audio <id>  -> present|gone
  if [ -f "$HELD_ROOT/.recordings/2026-01-01-1000-$1/them.m4a" ]; then printf 'present'; else printf 'gone'; fi
}

prune_held() { # prune_held <outfile> [extra env...]
  local out="$1"; shift
  capture 30 "$out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$HELD_ROOT" "$@" \
    /bin/bash "$QN" prune
}

# Answering yes deletes the held audio, and only where a rebuild is possible.
make_held_corpus
prune_held "$TMPROOT/held-yes.out" QN_ASSUME_YES=1
assert_eq "0" "$CAPTURE_CODE" "prune exits 0 when the held audio is accepted"
assert_eq "gone" "$(held_audio old-held)" "saying yes deletes a held meeting's audio"
assert_eq "gone" "$(held_audio old-full)" "an approved meeting is pruned as before"
assert_eq "present" "$(held_audio no-words)" "a held meeting with no words keeps its audio"

# The pruned held meeting must still be approvable, which is the whole premise.
assert_file "$HELD_ROOT/.recordings/2026-01-01-1000-old-held/.pruned" "the pruned held meeting is marked"
assert_file "$HELD_ROOT/.recordings/2026-01-01-1000-old-held/them.json" "its words survive the prune"

# No terminal to ask at means no. A cron job must never delete held audio.
make_held_corpus
prune_held "$TMPROOT/held-no.out"
assert_eq "0" "$CAPTURE_CODE" "prune exits 0 when nobody can be asked"
assert_eq "present" "$(held_audio old-held)" "a silent run leaves held audio alone"
assert_eq "gone" "$(held_audio old-full)" "a silent run still prunes approved meetings"
assert_contains "$(cat "$TMPROOT/held-no.out")" "awaiting approval, left alone" "it says what it left"

# A dry run deletes nothing and asks nothing.
make_held_corpus
prune_held "$TMPROOT/held-dry.out" QN_DRY_RUN=1 QN_ASSUME_YES=1
assert_eq "present" "$(held_audio old-held)" "a dry run deletes no held audio"
assert_eq "present" "$(held_audio old-full)" "a dry run deletes no approved audio"
assert_contains "$(cat "$TMPROOT/held-dry.out")" "would free" "a dry run still says what it would free"

# auto_prune runs unattended after a meeting, so it must never take held audio.
make_held_corpus
printf 'auto_prune = yes\nprune_days = 30\n' > "$TMPROOT/held-auto-config"
AUTO_WORK="$HELD_ROOT/.recordings/2026-01-01-1000-old-full"
printf 'Priya\n' > "$AUTO_WORK/attendees.txt"
capture 60 "$TMPROOT/held-auto.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$HELD_ROOT" \
  QN_CONFIG="$TMPROOT/held-auto-config" QN_ASSUME_YES=1 \
  QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
  /bin/bash "$QN" redo "$AUTO_WORK"
assert_eq "0" "$CAPTURE_CODE" "a recording with auto_prune on still exits 0"
assert_eq "present" "$(held_audio old-held)" "auto_prune never deletes held audio, even with yes set"

# --------------------------------------------------------------------------
section "qn doctor --mic"
# --------------------------------------------------------------------------
# doctor proves the permissions are granted. Only this proves the microphone
# carries sound, which is the one failure that costs a whole meeting.
guard_notes_dir

# A recorder stub that captures nothing, which is what a denied permission
# looks like from here.
MIC_RECORDER="$TMPROOT/mic-recorder"
cat > "$MIC_RECORDER" <<'STUBEOF'
#!/bin/bash
exit 0
STUBEOF
chmod +x "$MIC_RECORDER"

capture 40 "$TMPROOT/mic.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
  QN_RECORDER="$MIC_RECORDER" QN_MIC_SECONDS=1 \
  /bin/bash "$QN" doctor --mic
mic_out="$(cat "$TMPROOT/mic.out")"
assert_contains "$mic_out" "recording 1 seconds" "the mic test says it is recording"
assert_contains "$mic_out" "Microphone" "a captured-nothing run names the Microphone permission"
assert_contains "$mic_out" "Screen Recording" "a captured-nothing run names the Screen Recording permission"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "the mic test fails when nothing was captured" "it exited 0"
else
  pass "the mic test fails when nothing was captured"
fi

# It runs the ordinary doctor checks too, so one command answers everything.
assert_contains "$mic_out" "notes:" "doctor --mic still reports the setup"

# "doctor sync" is a fine meeting title once you ask for a recording by name.
capture 20 "$TMPROOT/mic-title.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
  QN_DRY_RUN=1 /bin/bash "$QN" record doctor sync
assert_contains "$(cat "$TMPROOT/mic-title.out")" "id=" "a meeting called 'doctor sync' still records"

# A near miss of a subcommand must never be mistaken for a recording.
capture 20 "$TMPROOT/typo.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
  QN_DRY_RUN=1 /bin/bash "$QN" doctorr 2>&1 || true
assert_contains "$(cat "$TMPROOT/typo.out")" "unknown command" "a mistyped subcommand is refused"

# A bad duration must fail here, naming the variable.
capture 20 "$TMPROOT/mic-bad.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$QN_NOTES_DIR" \
  QN_MIC_SECONDS=lots /bin/bash "$QN" doctor --mic
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "a bad QN_MIC_SECONDS is rejected" "it exited 0"
else
  pass "a bad QN_MIC_SECONDS is rejected"
fi
assert_contains "$(cat "$TMPROOT/mic-bad.out")" "QN_MIC_SECONDS" "the failure names the bad value"

# --------------------------------------------------------------------------
section "a recording that captured nothing"
# --------------------------------------------------------------------------
# merge.py prints a newline even with nothing to merge. The emptiness guard
# tested the file for bytes, so one newline passed it and Claude was asked to
# write notes about a meeting that was never recorded.
guard_notes_dir

SILENT_DIR="$TMPROOT/captured-nothing"
SILENT_ID="2026-03-03-0900-never-captured"
SILENT_WORK="$SILENT_DIR/.recordings/$SILENT_ID"
mkdir -p "$SILENT_WORK"
printf 'full\n' > "$SILENT_WORK/consent"
rm -f "$TMPROOT/silent-claude.log"
capture 40 "$TMPROOT/silent.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$SILENT_DIR" \
  QN_CLAUDE_LOG="$TMPROOT/silent-claude.log" \
  QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
  /bin/bash "$QN" redo "$SILENT_ID"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "a recording that captured nothing is refused" "it exited 0"
else
  pass "a recording that captured nothing is refused"
fi
assert_contains "$(cat "$TMPROOT/silent.out")" "nothing was transcribed" "it says nothing was transcribed"
if [ -e "$TMPROOT/silent-claude.log" ]; then
  fail "claude is never asked to write notes about silence" "the stub logged a call"
else
  pass "claude is never asked to write notes about silence"
fi
if [ -e "$SILENT_DIR/$SILENT_ID.md" ]; then
  fail "no note is written for a recording that captured nothing" "the note is there"
else
  pass "no note is written for a recording that captured nothing"
fi

# --------------------------------------------------------------------------
section "voice hints and qn confirm"
# --------------------------------------------------------------------------
# Them A / Them B is a hint. Only `qn confirm` turns one into a name, because
# only the person who was in the room can say.
guard_notes_dir

VOICE_DIR="$TMPROOT/voices"
VOICE_WORK="$VOICE_DIR/.recordings/2026-05-05-1100-planning"
mkdir -p "$VOICE_WORK"
printf 'full\n' > "$VOICE_WORK/consent"
printf 'Marco, Lena\n' > "$VOICE_WORK/attendees.txt"
printf 'audio' > "$VOICE_WORK/them.m4a"
printf 'audio' > "$VOICE_WORK/me.m4a"
cat > "$VOICE_WORK/them.json" <<'JSONEOF'
{"transcription":[
 {"offsets":{"from":0,"to":2000},"text":" shall I start the release"},
 {"offsets":{"from":9000,"to":11000},"text":" yes go ahead"}]}
JSONEOF
cat > "$VOICE_WORK/speakers.json" <<'JSONEOF'
[{"start_ms":0,"end_ms":3000,"speaker":"A"},
 {"start_ms":8000,"end_ms":12000,"speaker":"B"}]
JSONEOF
python3 "$ROOT/lib/merge.py" "$VOICE_WORK" > "$VOICE_WORK/transcript.txt"
assert_contains "$(cat "$VOICE_WORK/transcript.txt")" "Them A:" "the transcript carries a voice hint"
assert_contains "$(cat "$VOICE_WORK/transcript.txt")" "Them B:" "each voice gets its own letter"

voice_qn() { # voice_qn <outfile> <args...>
  local out="$1"; shift
  capture 30 "$out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$VOICE_DIR" \
    QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
    /bin/bash "$QN" "$@"
}

# A letter nobody spoke must be refused, and must say which letters exist.
voice_qn "$TMPROOT/confirm-bad.out" confirm "2026-05-05-1100-planning" Z "Nobody"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "confirming a voice that is not there fails" "it exited 0"
else
  pass "confirming a voice that is not there fails"
fi
assert_contains "$(cat "$TMPROOT/confirm-bad.out")" "it has: A B" "the refusal lists the voices it does have"

# Not a letter at all.
voice_qn "$TMPROOT/confirm-num.out" confirm "2026-05-05-1100-planning" 1 "Marco"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "a voice must be a single letter" "it exited 0"
else
  pass "a voice must be a single letter"
fi

# The real thing.
voice_qn "$TMPROOT/confirm.out" confirm "2026-05-05-1100-planning" a "Marco"
assert_eq "0" "$CAPTURE_CODE" "qn confirm exits 0"
assert_contains "$(cat "$VOICE_WORK/transcript.txt")" "Marco:" "a confirmed voice becomes the person"
assert_missing "$(cat "$VOICE_WORK/transcript.txt")" "Them A:" "the letter is gone once you have said who it was"
assert_contains "$(cat "$VOICE_WORK/transcript.txt")" "Them B:" "an unconfirmed voice keeps its letter"
assert_eq "A=Marco" "$(cat "$VOICE_WORK/confirmed.txt")" "the answer is kept beside the audio"

# A redo re-runs merge.py, so the answer has to survive it.
voice_qn "$TMPROOT/confirm-redo.out" redo "2026-05-05-1100-planning"
assert_eq "0" "$CAPTURE_CODE" "qn redo exits 0 after a confirmation"
assert_contains "$(cat "$VOICE_WORK/transcript.txt")" "Marco:" "a confirmation survives qn redo"

# And it reaches the note's frontmatter, marked as confirmed rather than guessed.
voice_note="$VOICE_DIR/2026-05-05-1100-planning.md"
assert_file "$voice_note" "qn redo writes the note"
assert_contains "$(cat "$voice_note")" "speaker_map:" "the note records who each voice was"
assert_contains "$(cat "$voice_note")" "A: Marco (confirmed)" "your answer is recorded as confirmed"

# Claude's own guess is recorded too, and marked as a guess.
cat > "$VOICE_WORK/summary.md" <<'SUMEOF'
## Summary
- planned the release.

## Decisions
- None

## My action items
- None

## Their action items
- None

## Open questions
- None

## Speakers
- A: Marco
- B: Lena
SUMEOF
# Correcting an answer must work, so this must not check the transcript for a
# letter that a previous confirmation already replaced.
voice_qn "$TMPROOT/confirm-guess.out" confirm "2026-05-05-1100-planning" A "Marco"
assert_eq "0" "$CAPTURE_CODE" "a voice can be confirmed again, to correct it"
guess_note="$(cat "$voice_note")"
assert_contains "$guess_note" "B: Lena (guess)" "Claude's guess is recorded as a guess"
assert_contains "$guess_note" "A: Marco (confirmed)" "your answer outranks Claude's guess"
assert_missing "$guess_note" "## Speakers" "the speakers section is lifted out of the note body"

# --------------------------------------------------------------------------
section "qn skip"
# --------------------------------------------------------------------------
# Some voice groups cannot be named honestly: two people the segmenter merged
# into one, or a video playing in the room. Skipping one takes it off the
# waiting list and tells the roster nothing. Naming it instead would teach a
# voiceprint under a name that is not one voice.
guard_notes_dir

SKIP_DIR="$TMPROOT/skip"
SKIP_WORK="$SKIP_DIR/.recordings/2026-05-06-1100-planning"
mkdir -p "$SKIP_WORK"
printf 'full\n' > "$SKIP_WORK/consent"
printf 'Marco, Lena\n' > "$SKIP_WORK/attendees.txt"
printf 'audio' > "$SKIP_WORK/them.m4a"
printf 'audio' > "$SKIP_WORK/me.m4a"
cat > "$SKIP_WORK/them.json" <<'JSONEOF'
{"transcription":[
 {"offsets":{"from":0,"to":2000},"text":" shall I start the release"},
 {"offsets":{"from":9000,"to":11000},"text":" yes go ahead"}]}
JSONEOF
cat > "$SKIP_WORK/speakers.json" <<'JSONEOF'
[{"start_ms":0,"end_ms":3000,"speaker":"A"},
 {"start_ms":8000,"end_ms":12000,"speaker":"B"}]
JSONEOF
# A roster that recognises B. Without a skip it names that group at every redo,
# which is what makes the test below prove something.
printf '{"A":[1.0,0.0],"B":[0.0,1.0]}\n' > "$SKIP_WORK/voiceprints.json"
printf '{"people":{"Lena":[{"from":"2026-05-01-1000-old","voice":"B","vector":[0.0,1.0]}]}}\n' \
  > "$SKIP_DIR/.voices.json"
printf 'A=Marco\n' > "$SKIP_WORK/confirmed.txt"
python3 "$ROOT/lib/merge.py" "$SKIP_WORK" > "$SKIP_WORK/transcript.txt"

skip_qn() { # skip_qn <outfile> <args...>
  local out="$1"; shift
  capture 30 "$out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$SKIP_DIR" \
    QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
    /bin/bash "$QN" "$@"
}

# A letter nobody spoke is refused here too, in the same words.
skip_qn "$TMPROOT/skip-bad.out" skip "2026-05-06-1100-planning" Q
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "skipping a voice that is not there fails" "it exited 0"
else
  pass "skipping a voice that is not there fails"
fi
assert_contains "$(cat "$TMPROOT/skip-bad.out")" "it has: A B" "the refusal lists the voices it does have"

# Your own answer must never be deleted quietly.
skip_qn "$TMPROOT/skip-confirmed.out" skip "2026-05-06-1100-planning" A
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "skipping a confirmed voice fails" "it exited 0"
else
  pass "skipping a confirmed voice fails"
fi
assert_contains "$(cat "$TMPROOT/skip-confirmed.out")" "as Marco" "the refusal says who you called it"
assert_eq "A=Marco" "$(cat "$SKIP_WORK/confirmed.txt")" "the confirmation is untouched"

# The roster names B, so there is something for the skip to take away.
python3 "$ROOT/lib/voices.py" "$SKIP_DIR" --match "$SKIP_WORK" > /dev/null
assert_eq "B=Lena" "$(cat "$SKIP_WORK/matched.txt" 2>/dev/null)" "the roster recognises the second voice"
python3 "$ROOT/lib/merge.py" "$SKIP_WORK" > "$SKIP_WORK/transcript.txt"
assert_contains "$(cat "$SKIP_WORK/transcript.txt")" "Lena:" "and its guess reaches the transcript"

# A name the roster guessed goes with the group. To say "this is not one
# person" and leave its name on the lines would contradict itself.
skip_qn "$TMPROOT/skip.out" skip "2026-05-06-1100-planning" b
assert_eq "0" "$CAPTURE_CODE" "qn skip exits 0"
assert_eq "B" "$(cat "$SKIP_WORK/skipped.txt")" "the skip is kept beside the audio"
assert_eq "" "$(cat "$SKIP_WORK/matched.txt" 2>/dev/null)" "the guess is dropped"
assert_contains "$(cat "$SKIP_WORK/transcript.txt")" "Them B:" "its lines go back to the letter"
assert_missing "$(cat "$SKIP_WORK/transcript.txt")" "Lena:" "the name it was given is gone"
assert_contains "$(cat "$SKIP_WORK/transcript.txt")" "Marco:" "the confirmed voice keeps its name"

# Skipping twice must not write the letter twice.
skip_qn "$TMPROOT/skip-twice.out" skip "2026-05-06-1100-planning" B
assert_eq "B" "$(cat "$SKIP_WORK/skipped.txt")" "skipping the same voice again changes nothing"

# The match runs again at every redo. This is the one that matters: the roster
# still recognises that voice, and must not be allowed to name it.
python3 "$ROOT/lib/voices.py" "$SKIP_DIR" --match "$SKIP_WORK" > /dev/null
assert_eq "" "$(cat "$SKIP_WORK/matched.txt" 2>/dev/null)" "re-matching does not name a skipped voice"
skip_qn "$TMPROOT/skip-redo.out" redo "2026-05-06-1100-planning"
assert_eq "0" "$CAPTURE_CODE" "qn redo exits 0 after a skip"
assert_eq "B" "$(cat "$SKIP_WORK/skipped.txt")" "a skip survives qn redo"
assert_eq "" "$(cat "$SKIP_WORK/matched.txt" 2>/dev/null)" "and qn redo does not name it either"

# A skip made by mistake has to be undoable, or one wrong word loses a voice.
skip_qn "$TMPROOT/skip-undo.out" confirm "2026-05-06-1100-planning" B "Lena"
assert_eq "0" "$CAPTURE_CODE" "confirming a skipped voice exits 0"
assert_eq "" "$(cat "$SKIP_WORK/skipped.txt")" "confirming a voice takes it off the skip list"
assert_eq "B=Lena" "$(cat "$SKIP_WORK/confirmed.txt" | grep '^B=')" "and the answer is recorded"

# The letter is not optional.
skip_qn "$TMPROOT/skip-usage.out" skip "2026-05-06-1100-planning"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "qn skip needs a letter" "it exited 0"
else
  pass "qn skip needs a letter"
fi

# --------------------------------------------------------------------------
section "a watch whose script changed under it"
# --------------------------------------------------------------------------
# `qn watch` runs for hours. Bash reads a script by byte offset, so editing it
# mid-run is a real hazard. Even the mild case leaves old functions in memory
# pointing at paths that moved. The watch must hand the meeting back rather
# than fail inside a helper, because the audio is already safe by then.
guard_notes_dir

STALE_QN="$TMPROOT/stale-qn"
cp "$QN" "$STALE_QN"
STALE_NOTES="$TMPROOT/stale-notes"
mkdir -p "$STALE_NOTES/.recordings"

# A watcher stub that starts a meeting, then stops it, then ends.
STALE_WATCHER="$TMPROOT/stale-watcher"
cat > "$STALE_WATCHER" <<'STUBEOF'
#!/bin/bash
printf 'START\tapp=zoom.us\twindow=Zoom Meeting\ttitle=changed under me\tattendees=\n'
# Long enough for the edit below to land before the meeting ends.
sleep 5
printf 'STOP\n'
sleep 1
STUBEOF
chmod +x "$STALE_WATCHER"

# A recorder stub that writes both tracks and exits, like the real one.
STALE_RECORDER="$TMPROOT/stale-recorder"
cat > "$STALE_RECORDER" <<'STUBEOF'
#!/bin/bash
printf 'audio' > "$1/them.m4a"
printf 'audio' > "$1/me.m4a"
STUBEOF
chmod +x "$STALE_RECORDER"

# Touch the copy into the future while the watch runs, which is what an edit
# looks like from the inside.
( sleep 2; touch -t 203001010000 "$STALE_QN" ) >/dev/null 2>&1 &
TOUCHER=$!
capture 30 "$TMPROOT/stale.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$STALE_NOTES" \
  QN_CONSENT=full QN_WATCHER="$STALE_WATCHER" QN_RECORDER="$STALE_RECORDER" \
  QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
  /bin/bash "$STALE_QN" watch
wait "$TOUCHER" 2>/dev/null || true

stale_out="$(cat "$TMPROOT/stale.out")"
assert_contains "$stale_out" "changed on disk" "a watch notices its own script changed"
assert_contains "$stale_out" "qn redo" "it names the command that finishes the meeting"
assert_contains "$stale_out" "recording is safe" "it says the recording is safe"

stale_work="$(find "$STALE_NOTES/.recordings" -name 'them.m4a' -print -quit 2>/dev/null)"
assert_file "$stale_work" "the audio survives a script that changed mid-watch"

# A helper that is genuinely gone must name itself, not fail inside python.
HELPER_WORK="$STALE_NOTES/.recordings/2026-01-01-1000-no-helper"
mkdir -p "$HELPER_WORK"
printf 'full\n' > "$HELPER_WORK/consent"
printf 'audio' > "$HELPER_WORK/them.m4a"
printf 'audio' > "$HELPER_WORK/me.m4a"
BARE="$TMPROOT/bare"
mkdir -p "$BARE/lib"
cp "$QN" "$BARE/qn"
cp "$ROOT/prompt.md" "$BARE/prompt.md"
capture 20 "$TMPROOT/no-helper.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$STALE_NOTES" \
  QN_MODEL="$TMPROOT/model.bin" QN_VAD_MODEL="$TMPROOT/no-vad.bin" \
  /bin/bash "$BARE/qn" redo "2026-01-01-1000-no-helper"
if [ "$CAPTURE_CODE" -eq 0 ]; then
  fail "a missing helper is reported" "it exited 0"
else
  pass "a missing helper is reported"
fi
assert_contains "$(cat "$TMPROOT/no-helper.out")" "health.py is missing" "the failure names the missing helper"
assert_contains "$(cat "$TMPROOT/no-helper.out")" "the audio is safe" "the failure says the audio is safe"

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
# Assert the check itself passed, not merely that the repo path was printed.
# The path appears in the `code:` line either way, so a broken check looked
# fine here while `qn doctor` was reporting a failure to the user.
assert_contains "$(cat "$TMPROOT/linked.out")" "qn resolves to its repo" \
  "a symlinked qn reports on its own repo"
assert_missing "$(cat "$TMPROOT/linked.out")" "is not a quiet-notetaker checkout" \
  "a symlinked qn resolves to a real checkout"
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
section "doctor reports the privacy boundary"
# --------------------------------------------------------------------------
# A held meeting is invisible to every query in this tool, so the user cannot
# ask Claude what it is hiding. `qn doctor` answers that from outside.
guard_notes_dir

AUDIT_NOTES="$TMPROOT/audit-notes"
mkdir -p "$AUDIT_NOTES"
cp "$full_note" "$AUDIT_NOTES/"
cp "$local_note" "$AUDIT_NOTES/"
cp "$QN_NOTES_DIR/people.md" "$AUDIT_NOTES/" 2>/dev/null || true

capture 30 "$TMPROOT/audit.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$AUDIT_NOTES" \
  /bin/bash "$QN" doctor
audit_out="$(cat "$TMPROOT/audit.out")"
assert_contains "$audit_out" "meetings: 2" "doctor counts the notes on disk"
assert_contains "$audit_out" "indexed:  1 visible to Claude" "doctor counts what Claude can see"
assert_contains "$audit_out" "held:     1 private" "doctor counts what is held back"

# The roster lives beside the notes and is not a meeting.
assert_missing "$audit_out" "meetings: 3" "the roster is not counted as a meeting"

# An empty folder must say nothing rather than print three zeroes.
mkdir -p "$TMPROOT/audit-empty"
capture 30 "$TMPROOT/audit-empty.out" env PATH="$STUB:$PATH" \
  QN_NOTES_DIR="$TMPROOT/audit-empty" /bin/bash "$QN" doctor
assert_missing "$(cat "$TMPROOT/audit-empty.out")" "meetings: 0" "an empty folder reports no counts"

# The number shown must be the number a search can actually reach.
capture 30 "$TMPROOT/audit-index.out" env PATH="$STUB:$PATH" QN_NOTES_DIR="$AUDIT_NOTES" \
  /bin/bash "$QN" index
audit_rows="$(python3 -c "
import sqlite3, sys
print(sqlite3.connect(sys.argv[1]).execute('SELECT count(*) FROM meetings').fetchone()[0])
" "$AUDIT_NOTES/.index.db")"
assert_eq "1" "$audit_rows" "the index holds exactly the number doctor called visible"

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
