"""Contact sheet of a clip with an x-grid every 60 px (720-wide coords), for reading off swap regions.

Usage: .venv/bin/python scripts/grid_sheet.py clip.mp4 out.jpg [frames=10]
"""
import sys

import cv2
import numpy as np

src, out = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
cap = cv2.VideoCapture(src)
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
fps = cap.get(cv2.CAP_PROP_FPS) or 30
tiles = []
for k in range(n):
    idx = int(k * (total - 1) / (n - 1))
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, f = cap.read()
    if not ok:
        continue
    f = cv2.resize(f, (720, 1280))
    for x in range(0, 720, 60):
        cv2.line(f, (x, 0), (x, 1279), (0, 255, 255) if x % 120 == 0 else (0, 160, 160), 2 if x % 120 == 0 else 1)
        if x % 120 == 0:
            cv2.putText(f, str(x), (x + 4, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 3)
    cv2.putText(f, f"{idx / fps:.1f}s", (20, 1250), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 4)
    tiles.append(cv2.resize(f, (360, 640)))
while len(tiles) % 5:
    tiles.append(np.zeros_like(tiles[0]))
rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]
cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
print("wrote", out, f"{total} frames")
