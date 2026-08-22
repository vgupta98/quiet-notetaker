#!/usr/bin/env bash
# Synthesise a two-track recording without holding a meeting.
#
#   test/make_audio.sh <output-dir> [--seconds 30] [--silent-me] [--missing-them]
#
# Writes them.m4a and me.m4a into <output-dir>, in the same shape that
# recorder/main.swift produces: 48 kHz, stereo, AAC, 96 kbit/s.
#
#   them.m4a   the other side of the call, two turns
#   me.m4a     the microphone, two turns, offset so the tracks interleave
#
#   --silent-me      me.m4a is pure silence, for capture-health tests
#   --missing-them   no them.m4a at all, for missing-track tests
#
# macOS `say` and `ffmpeg` are required.
set -euo pipefail

out=""
seconds=30
silent_me=0
missing_them=0

usage() {
  printf 'usage: make_audio.sh <output-dir> [--seconds N] [--silent-me] [--missing-them]\n' >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --seconds)      seconds="${2:-}"; shift 2 ;;
    --silent-me)    silent_me=1; shift ;;
    --missing-them) missing_them=1; shift ;;
    -h|--help)      usage ;;
    -*)             printf 'make_audio.sh: unknown option %s\n' "$1" >&2; usage ;;
    *)              if [ -n "$out" ]; then usage; fi
                    out="$1"; shift ;;
  esac
done

[ -n "$out" ] || usage
case "$seconds" in
  ''|*[!0-9]*) printf 'make_audio.sh: --seconds needs a whole number\n' >&2; exit 2 ;;
esac
[ "$seconds" -ge 4 ] || { printf 'make_audio.sh: --seconds must be 4 or more\n' >&2; exit 2; }

command -v say    >/dev/null || { printf 'make_audio.sh: say is missing (macOS only)\n' >&2; exit 1; }
command -v ffmpeg >/dev/null || { printf 'make_audio.sh: ffmpeg is missing — run: brew install ffmpeg\n' >&2; exit 1; }

mkdir -p "$out"
out="$(cd "$out" && pwd)"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/qn-audio.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

# Pick a voice that this machine actually has, otherwise the system default.
voice_or_default() {
  if say -v '?' 2>/dev/null | grep -q "^$1  *"; then printf '%s' "$1"; fi
}
them_voice="$(voice_or_default Daniel)"
me_voice="$(voice_or_default Samantha)"

speak() {  # speak <outfile.aiff> <voice-or-empty> <text>
  if [ -n "$2" ]; then
    say -v "$2" -o "$1" "$3"
  else
    say -o "$1" "$3"
  fi
}

THEM_1="Thanks for making the time. I wanted to walk through the migration plan before we commit to a date."
THEM_2="That works for me. I will confirm the storage quota with the infrastructure team and send it over."
ME_1="Sounds good. We ran it against a copy of production last night and it took about forty minutes."
ME_2="Right, I will write the rollout up and post it for review by Friday morning."

# Four turns, evenly spread: them, me, them, me.
quarter=$(( seconds / 4 ))
d_t1=0
d_m1=$(( quarter * 1000 ))
d_t2=$(( quarter * 2 * 1000 ))
d_m2=$(( quarter * 3 * 1000 ))

# Mix two delayed speech clips into one 48 kHz stereo AAC track.
mix_track() {  # mix_track <a.aiff> <delay-a-ms> <b.aiff> <delay-b-ms> <out.m4a>
  ffmpeg -nostdin -v error -y -i "$1" -i "$3" -filter_complex \
    "[0:a]aformat=channel_layouts=stereo,adelay=$2|$2[a];[1:a]aformat=channel_layouts=stereo,adelay=$4|$4[b];[a][b]amix=inputs=2:normalize=0,apad=whole_dur=$seconds" \
    -t "$seconds" -ar 48000 -ac 2 -c:a aac -b:a 96k "$5"
}

if [ "$missing_them" -eq 1 ]; then
  rm -f "$out/them.m4a"
  printf '  them.m4a: skipped (--missing-them)\n' >&2
else
  speak "$tmp/t1.aiff" "$them_voice" "$THEM_1"
  speak "$tmp/t2.aiff" "$them_voice" "$THEM_2"
  mix_track "$tmp/t1.aiff" "$d_t1" "$tmp/t2.aiff" "$d_t2" "$out/them.m4a"
  printf '  them.m4a: %s\n' "${them_voice:-default voice}" >&2
fi

if [ "$silent_me" -eq 1 ]; then
  ffmpeg -nostdin -v error -y -f lavfi -i anullsrc=r=48000:cl=stereo \
    -t "$seconds" -ar 48000 -ac 2 -c:a aac -b:a 96k "$out/me.m4a"
  printf '  me.m4a: silence (--silent-me)\n' >&2
else
  speak "$tmp/m1.aiff" "$me_voice" "$ME_1"
  speak "$tmp/m2.aiff" "$me_voice" "$ME_2"
  mix_track "$tmp/m1.aiff" "$d_m1" "$tmp/m2.aiff" "$d_m2" "$out/me.m4a"
  printf '  me.m4a: %s\n' "${me_voice:-default voice}" >&2
fi

printf '%s\n' "$out"
