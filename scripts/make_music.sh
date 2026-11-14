#!/bin/bash
# Cut the soundtrack section into assets/music.mp3 (36 s; the stitcher trims to the film's length and fades the last 1 s).
# Usage: scripts/make_music.sh [start_seconds]   (default 42: the loudest, steadiest stretch of the track)
#
# Loudness: the source track is mastered hot (-5.7 LUFS, +3.2 dBTP), which clips on phone speakers and gets turned
# down unevenly by social apps. Two-pass loudnorm brings the cut to -14 LUFS / -1.5 dBTP (the social-video standard),
# so every film is at the right level without a per-job audio pass.
set -euo pipefail
cd "$(dirname "$0")/.."
START=${1:-42}
SRC=raw/music/AUDIO-2026-11-13-06-43-34.mp3
TARGET="I=-14:TP=-1.5:LRA=11"
STATS=$(ffmpeg -hide_banner -nostats -ss "$START" -t 36 -i "$SRC" -af "loudnorm=$TARGET:print_format=json" -f null - 2>&1 \
  | sed -n '/^{/,/^}/p')
m() { echo "$STATS" | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
ffmpeg -hide_banner -loglevel error -y -ss "$START" -t 36 -i "$SRC" \
  -af "loudnorm=$TARGET:measured_I=$(m input_i):measured_TP=$(m input_tp):measured_LRA=$(m input_lra):measured_thresh=$(m input_thresh):offset=$(m target_offset):linear=true,afade=t=in:st=0:d=0.4,aresample=48000" \
  -c:a libmp3lame -q:a 2 assets/music.mp3
echo "wrote assets/music.mp3 from ${START}s"
