"""Defect scan for a finished visitor film. Prints PASS/FAIL per check and writes a review sheet.

Usage: .venv/bin/python scripts/qa_film.py final.mp4 visitor_photo.jpg [template_id] [out_dir]

Checks (each one a defect a visitor would notice):
  format    720x1280, H.264 + AAC, duration equals the template timeline
  cuts      no hard cut inside the swapped take (ffmpeg scdet score > 3; the title's first-frame flash is ignored)
  freeze    no frozen video longer than 0.5 s inside the take (a stalled or short swap output)
  black     no black frames anywhere
  audio     music present and not clipping (EBU R128 true peak < -0.5 dBTP)
Sheet:      10 frames across the take + the visitor's face crops next to their photo, for the human likeness check.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import os  # noqa: E402

os.environ.setdefault("OPERATOR_PIN", "qa")
from app import config, ffmpeg_builder as fb  # noqa: E402

film, photo = Path(sys.argv[1]), Path(sys.argv[2])
tid = sys.argv[3] if len(sys.argv) > 3 else "men"
out_dir = Path(sys.argv[4]) if len(sys.argv) > 4 else film.parent
tl = fb.timeline(config.load_templates()[tid])
take0, take1 = tl.a_start + fb.XF, tl.hold_start            # the swapped take, between the dissolves
results = []


def check(name, ok, detail):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:7s} {detail}")


def ff(*args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(film), *args, "-f", "null", "-"],
                          capture_output=True, text=True).stderr


probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                   "stream=codec_type,codec_name,width,height:format=duration", "-of", "json",
                                   str(film)], capture_output=True, text=True).stdout)
v = next(s for s in probe["streams"] if s["codec_type"] == "video")
a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
dur = float(probe["format"]["duration"])
check("format", (v["width"], v["height"]) == (720, 1280) and v["codec_name"] == "h264" and a is not None
      and abs(dur - tl.total) < 0.15, f"{v['width']}x{v['height']} {v['codec_name']}+{a and a['codec_name']} "
      f"{dur:.2f}s (timeline {tl.total:.2f}s)")

cuts = [float(t) for t in re.findall(r"lavfi\.scd\.time=([0-9.]+)", ff("-vf", "scdet=threshold=3,metadata=print"))]
inside = [t for t in cuts if take0 <= t <= take1]
check("cuts", not inside, f"hard cuts inside take {take0:.1f}-{take1:.1f}s: {inside or 'none'} (all: {cuts})")

freeze = ff("-vf", "freezedetect=n=0.0008:d=0.5")
starts = [float(t) for t in re.findall(r"freeze_start: ([0-9.]+)", freeze)]
in_take = [t for t in starts if take0 <= t < take1 - 0.5]
check("freeze", not in_take, f"frozen >0.5 s inside take: {in_take or 'none'}")

black = [float(t) for t in re.findall(r"black_start:([0-9.]+)", ff("-vf", "blackdetect=d=0.1:pix_th=0.06"))]
unplanned = [t for t in black if t < tl.total - 1.0]   # the end card fades to black over its last 0.6 s by design
check("black", not unplanned, f"black segments: {unplanned or 'none'} (planned end fade: {[t for t in black if t not in unplanned]})")

loud = ff("-af", "ebur128=peak=true")
peak = re.findall(r"Peak:\s+(-?[0-9.]+|-inf) dBFS", loud)
lufs = re.findall(r"I:\s+(-?[0-9.]+) LUFS", loud)
pk = float(peak[-1]) if peak and peak[-1] != "-inf" else -99
check("audio", lufs and float(lufs[-1]) > -40 and pk < -0.5, f"integrated {lufs[-1] if lufs else '?'} LUFS, "
      f"true peak {pk} dBTP")

# Review sheet: frames across the take, then face crops beside the photo.
cap = cv2.VideoCapture(str(film))
casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
frames, faces = [], []
for t in np.linspace(take0, take1, 10):
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, f = cap.read()
    if not ok:
        continue
    tile = cv2.resize(f, (270, 480))
    cv2.putText(tile, f"{t:.1f}s", (6, 470), 0, 0.8, (255, 255, 255), 2)
    frames.append(tile)
    g = cv2.cvtColor(f[:, :470], cv2.COLOR_BGR2GRAY)   # visitor side of the frame
    fs, _, w = casc.detectMultiScale3(g, 1.08, 4, minSize=(24, 24), outputRejectLevels=True)
    if len(fs):
        x, y, ww, hh = fs[int(np.argmax(np.ravel(w)))]
        m = int(ww * 0.9)
        cx, cy = x + ww // 2, y + hh // 2
        faces.append(cv2.resize(f[max(0, cy - m):cy + m, max(0, cx - m):cx + m], (160, 160)))
ref = cv2.imread(str(photo))
fs, _, w = casc.detectMultiScale3(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), 1.08, 5, minSize=(28, 28),
                                  outputRejectLevels=True)
if len(fs):
    x, y, ww, hh = fs[int(np.argmax(np.ravel(w)))]
    m = int(ww * 0.9)
    ref = ref[max(0, y + hh // 2 - m):y + hh // 2 + m, max(0, x + ww // 2 - m):x + ww // 2 + m]
ref = cv2.resize(ref, (160, 160))
cv2.putText(ref, "PHOTO", (4, 18), 0, 0.6, (0, 255, 255), 2)
row1 = np.hstack(frames)
row2 = np.hstack([ref] + faces[:12])
row2 = np.hstack([row2, np.zeros((160, max(0, row1.shape[1] - row2.shape[1]), 3), np.uint8)])[:, :row1.shape[1]]
sheet = out_dir / f"{film.stem}_qa.jpg"
cv2.imwrite(str(sheet), np.vstack([row1, row2]), [cv2.IMWRITE_JPEG_QUALITY, 85])
print(f"sheet {sheet} ({len(faces)} face crops)")
print("ALL AUTOMATED CHECKS PASSED" if all(results) else "DEFECTS FOUND")
sys.exit(0 if all(results) else 1)
