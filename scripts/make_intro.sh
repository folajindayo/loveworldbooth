#!/bin/bash
# Build assets/intro.mp4 (4.4 s, 720x1280, 24 fps, no audio):
#   gold-glitter "LOVEWORLD EXPO 2026" title (3.0 s, scripts/make_title.py) ~ dissolve ~ the TSION / LOVEWORLD EXPO
#   ring, restored from the real footage with a slow push-in (1.8 s).
# raw/expo/intro_ring_clean.mp4 is the restored ring shot; raw/expo/intro_title_glitter.mp4 is make_title.py's output.
set -euo pipefail
cd "$(dirname "$0")/.."
TITLE=raw/expo/intro_title_glitter.mp4
RING=raw/expo/intro_ring_clean.mp4
[ -f "$TITLE" ] || { .venv/bin/python scripts/make_title.py "$TITLE" 3.0; }
ffmpeg -hide_banner -loglevel error -y \
  -i "$TITLE" \
  -ss 1.0 -t 1.8 -i "$RING" \
  -filter_complex "\
[0:v]setsar=1,fps=24,format=yuv420p,settb=1/24[a];\
[1:v]scale=720:1280:force_original_aspect_ratio=increase:flags=lanczos,crop=720:1280,setsar=1,fps=24,format=yuv420p,setpts=PTS-STARTPTS,settb=1/24[b];\
[a][b]xfade=transition=fade:duration=0.4:offset=2.6[v]" \
  -map "[v]" -an -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -t 4.4 assets/intro.mp4
echo "wrote assets/intro.mp4"
