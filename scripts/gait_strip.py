"""Frames every 1/6 s across a window, cropped to the lower body, for counting steps (walking pace).

Usage: .venv/bin/python scripts/gait_strip.py video.mp4 out.jpg t0 t1 [x0 y0 x1 y1 as fractions]
A natural walk is ~1.8-2.2 steps/s; AI "slow motion" walks come out near 1 step/s.
"""
import sys

import cv2
import numpy as np

src, out, t0, t1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
x0, y0, x1, y1 = (float(v) for v in sys.argv[5:9]) if len(sys.argv) > 8 else (0, 0.45, 1, 1)
cap = cv2.VideoCapture(src)
tiles, t = [], t0
while t <= t1 + 1e-6:
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, f = cap.read()
    if not ok:
        break
    h, w = f.shape[:2]
    c = cv2.resize(f[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)], (160, 200))
    cv2.putText(c, f"{t:.2f}", (4, 16), 0, 0.5, (0, 255, 255), 1)
    tiles.append(c)
    t += 1 / 6
rows = [np.hstack(tiles[i:i + 12] + [np.zeros_like(tiles[0])] * (12 - len(tiles[i:i + 12]))) for i in range(0, len(tiles), 12)]
cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
print("wrote", out, len(tiles), "frames")
