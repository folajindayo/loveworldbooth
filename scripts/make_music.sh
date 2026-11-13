#!/bin/bash
# Cut the soundtrack section into assets/music.mp3 (26 s; the stitcher trims to 25 s and fades the last 1 s).
# Usage: scripts/make_music.sh [start_seconds]   (default 42: the loudest, steadiest stretch of the track)
set -euo pipefail
cd "$(dirname "$0")/.."
START=${1:-42}
ffmpeg -hide_banner -loglevel error -y -ss "$START" -t 26 -i raw/music/AUDIO-2026-11-13-06-43-34.mp3 \
  -af "afade=t=in:st=0:d=0.4" -c:a libmp3lame -q:a 2 assets/music.mp3
echo "wrote assets/music.mp3 from ${START}s"
