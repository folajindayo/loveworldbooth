#!/bin/bash
# Build assets/intro.mp4 (3.0 s, 720x1280, 30 fps, no audio) from the Expo footage in raw/expo/.
# Cuts: stadium crowd (0.9 s) -> "LOVEWORLD EXPO" ring sign over the stage (0.9 s) -> title card frozen (1.2 s).
# The crowd clip is a split-screen TikTok: its bottom half is placed over a blurred copy of itself.
set -euo pipefail
cd "$(dirname "$0")/.."
CROWD=raw/expo/snaptik_7707051718483201300_v3.mp4
HALL=raw/expo/video_2026-11-13_08-13-21.mp4
FIT="scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=30,format=yuv420p"
ffmpeg -hide_banner -loglevel error -y \
  -ss 29.5 -t 0.9 -i "$CROWD" \
  -ss 15.6 -t 0.9 -i "$HALL" \
  -ss 9.8  -t 0.1 -i "$HALL" \
  -filter_complex "\
[0:v]crop=iw:ih/2:0:ih/2,split[cr1][cr2];\
[cr1]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,gblur=sigma=30,eq=brightness=-0.15[bg];\
[cr2]scale=720:-2[fg];[bg][fg]overlay=0:(H-h)/2,setsar=1,fps=30,format=yuv420p,setpts=PTS-STARTPTS[a];\
[1:v]$FIT,setpts=PTS-STARTPTS[b];[2:v]$FIT,trim=end_frame=1,tpad=stop_mode=clone:stop_duration=1.2,setpts=PTS-STARTPTS[c];\
[a][b][c]concat=n=3:v=1:a=0[v]" \
  -map "[v]" -an -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -t 3 assets/intro.mp4
echo "wrote assets/intro.mp4"
