"""Gold-glitter title card (the "Moments Ago" style from the LoveWorld Awards 2022 recap), drawn from scratch.

Beats: white-gold bloom flash -> falling glitter streaks -> two swirling gold glitter clouds on a dark ground
-> metallic title blurs in with a warm glow and a light sweep -> slow push-in throughout.

Usage: .venv/bin/python scripts/make_title.py [out.mp4] [seconds]
Writes 720x1280 24 fps H.264 (default raw/generated/title_glitter.mp4, 3.0 s).
"""
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "raw/generated/title_glitter.mp4"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
W, H, FPS = 720, 1280, 24
N = round(SECONDS * FPS)
FONT = "/System/Library/Fonts/Avenir Next.ttc"
TITLE = ["LOVEWORLD", "EXPO 2026"]
SUBTITLE = "CERTIFICATE OF PRESENCE"
rng = np.random.default_rng(2026)

GOLD = np.array([1.00, 0.74, 0.38], np.float32)
PALE = np.array([1.00, 0.93, 0.78], np.float32)


# ---------------------------------------------------------------- particles
def wing(n, side):
    """Glitter flowing along a band down one edge of the frame, bowing inward at mid-height."""
    spray = rng.random(n) < 0.22
    return dict(side=side, u=rng.random(n) * 1.2 - 0.1, off=np.abs(rng.normal(0, 1, n)) * np.where(spray, 2.4, 1.0),
                jit=rng.normal(0, 1, n) * 6, b=(0.3 + 0.7 * rng.random(n)) * np.where(rng.random(n) < 0.05, 3.2, 1.0),
                f=0.6 + 2.6 * rng.random(n), ph=rng.random(n) * 6.283, mix=rng.random(n) ** 2)


SWIRLS = [wing(16000, -1), wing(16000, 1)]
BOKEH = dict(x=rng.random(70) * W, y=rng.random(70) * H, r=3 + rng.random(70) * 10, b=0.04 + 0.1 * rng.random(70),
             vy=-4 - 10 * rng.random(70), hue=rng.random(70))
DUST = dict(x=rng.random(1600) * W, y=rng.random(1600) * H, b=0.15 + 0.6 * rng.random(1600) ** 3,
            f=0.5 + 3 * rng.random(1600), ph=rng.random(1600) * 6.283, vx=rng.normal(0, 4, 1600))
RAIN = dict(x=rng.random(520) * W, y0=-rng.random(520) * H, v=500 + 500 * rng.random(520), b=0.3 + 0.7 * rng.random(520))


def splat(buf, xs, ys, colors):
    ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    np.add.at(buf, (ys[ok].astype(int), xs[ok].astype(int)), colors[ok])


def draw_particles(t):
    buf = np.zeros((H, W, 3), np.float32)
    for sw in SWIRLS:
        u = (sw["u"] + sw["side"] * -0.05 * t) % 1.2 - 0.1                 # glitter flows along the band
        bow = np.sin(np.clip(u, 0, 1) * math.pi) ** 1.4
        ph = 0.0 if sw["side"] < 0 else 2.1                              # each side clumps differently
        centre = 12 + 88 * bow + 8 * np.sin(t * 1.3 + u * 7) + bow * (26 * np.sin(u * 9 + ph) + 14 * np.sin(u * 17 + 2 * ph))
        thick = (14 + 34 * bow) * (0.65 + 0.7 * (0.5 + 0.5 * np.sin(u * 11 + 3 * ph + t * 0.4)))
        inward = centre + sw["off"] * thick * 0.9                        # spread toward the middle of the frame
        x = np.where(sw["side"] < 0, inward, W - inward) + sw["jit"]
        y = u * H
        core = np.exp(-sw["off"] ** 2 * 0.5)                             # brightest along the spine
        tw = sw["b"] * (0.45 + 0.55 * np.sin(sw["f"] * t * 6.283 + sw["ph"])) * (0.35 + 0.9 * core)
        col = (GOLD[None] * (1 - sw["mix"][:, None]) + PALE[None] * sw["mix"][:, None]) * tw[:, None] * 2.4
        splat(buf, x, y, col)
    tw = DUST["b"] * (0.4 + 0.6 * np.sin(DUST["f"] * t * 6.283 + DUST["ph"]) ** 2)
    splat(buf, (DUST["x"] + DUST["vx"] * t) % W, (DUST["y"] - 14 * t) % H, PALE[None] * tw[:, None] * 1.3)
    if t < 1.6:                                                       # falling glitter streaks at the start
        fade = (1 - t / 1.6) ** 1.5
        for k in range(0, 28, 3):
            ys = RAIN["y0"] + RAIN["v"] * t - k
            splat(buf, RAIN["x"], ys, GOLD[None] * (RAIN["b"] * fade * (1 - k / 28))[:, None] * 1.2)
    sharp = cv2.GaussianBlur(buf, (0, 0), 0.7)
    out = sharp * 1.3 + cv2.GaussianBlur(buf, (0, 0), 3) * 0.9 + cv2.GaussianBlur(buf, (0, 0), 16) * 1.6
    orbs = np.zeros((H, W, 3), np.float32)                             # soft out-of-focus orbs for depth
    for x, y, r, b, vy, hue in zip(BOKEH["x"], BOKEH["y"], BOKEH["r"], BOKEH["b"], BOKEH["vy"], BOKEH["hue"]):
        col = tuple(float(v) for v in (GOLD if hue < 0.7 else np.array([0.55, 0.45, 0.85], np.float32)) * b)
        cv2.circle(orbs, (int(x), int((y + vy * t) % H)), int(r), col, -1, cv2.LINE_AA)
    return out + cv2.GaussianBlur(orbs, (0, 0), 2.5)


# ---------------------------------------------------------------- background
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
dist = np.sqrt(((xx - W / 2) / W) ** 2 + ((yy - H * 0.47) / H) ** 2)
BG = np.clip(1 - dist * 1.6, 0, 1)[..., None] * np.array([0.065, 0.048, 0.04], np.float32) + 0.012
BG += np.clip(1 - yy / (H * 0.5), 0, 1)[..., None] * np.array([0.02, 0.012, 0.035], np.float32)   # violet haze on top


# ---------------------------------------------------------------- title
def tracked(draw, text, font, cx, y, tracking, fill):
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def title_layer():
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    big = ImageFont.truetype(FONT, 74, index=8)
    sub = ImageFont.truetype(FONT, 19, index=5)
    y = 520
    for line in TITLE:
        tracked(d, line, big, W / 2, y, 9, 255)
        y += 86
    sub_mask = Image.new("L", (W, H), 0)
    tracked(ImageDraw.Draw(sub_mask), SUBTITLE, sub, W / 2, y + 22, 7, 255)
    return np.asarray(mask, np.float32) / 255, np.asarray(sub_mask, np.float32) / 255


TMASK, SMASK = title_layer()
GRAD = np.interp(yy[:, 0], [520, 560, 585, 606, 646, 671, 692], [0.98, 0.70, 0.92, 0.98, 0.70, 0.92, 0.98])[:, None, None]
METAL = np.broadcast_to(GRAD * np.array([1.0, 0.99, 0.97], np.float32), (H, W, 3))


def draw_title(t):
    a = np.clip((t - 0.25) / 0.65, 0, 1)
    a = a * a * (3 - 2 * a)                                            # smoothstep in
    if a <= 0:
        return np.zeros((H, W, 3), np.float32), 0.0
    blur = 14 * (1 - a) + 0.01
    scale = 1.12 - 0.12 * a
    M = cv2.getRotationMatrix2D((W / 2, H * 0.47), 0, scale)
    tm = cv2.warpAffine(cv2.GaussianBlur(TMASK, (0, 0), blur), M, (W, H))
    sm = cv2.warpAffine(cv2.GaussianBlur(SMASK, (0, 0), blur), M, (W, H))
    sweep_x = -250 + (t - 0.8) / 1.4 * (W + 500)                         # light sweep across the letters
    band = np.exp(-(((xx + (yy - 600) * 0.45) - sweep_x) / 55) ** 2)[..., None]
    soft = cv2.GaussianBlur(tm, (0, 0), 1.6)
    gx = cv2.Sobel(soft, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(soft, cv2.CV_32F, 0, 1, ksize=3)
    shade = np.clip(0.80 + (gx * 0.55 + gy * 0.75) * 0.9, 0.38, 1.25)[..., None]   # bevel lit from the top left
    letters = METAL * shade * tm[..., None] * (0.92 + band * 0.55)
    glow = cv2.GaussianBlur(tm, (0, 0), 6)[..., None] * GOLD * 0.32 + cv2.GaussianBlur(tm, (0, 0), 24)[..., None] * GOLD * 0.22
    subtitle = sm[..., None] * np.array([0.96, 0.86, 0.66], np.float32) + cv2.GaussianBlur(sm, (0, 0), 6)[..., None] * GOLD * 0.35
    return (letters + glow + subtitle) * a, a


# ---------------------------------------------------------------- render
enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "slow",
                        "-crf", "15", "-pix_fmt", "yuv420p", str(OUT)], stdin=subprocess.PIPE)
for i in range(N):
    t = i / FPS
    p = draw_particles(t)
    p = p / (1 + p * 0.6)                                               # roll-off: dense glitter glows, never clips
    text, a = draw_title(t)
    tm = np.clip(text.max(axis=2, keepdims=True), 0, 1)
    frame = (BG + p) * (1 - 0.7 * tm) + text                            # glitter sits behind the letters
    if t < 0.45:                                                        # short warm bloom flash resolving
        k = (1 - t / 0.45) ** 2
        frame = cv2.GaussianBlur(frame, (0, 0), 1 + 20 * k) * (1 + 1.2 * k) + k * 0.9 * np.array([1.0, 0.86, 0.62], np.float32)
    push = 1 + 0.05 * (t / SECONDS)
    frame = cv2.warpAffine(frame, cv2.getRotationMatrix2D((W / 2, H / 2), 0, push), (W, H), borderMode=cv2.BORDER_REFLECT)
    enc.stdin.write((np.clip(frame, 0, 1) * 255).astype(np.uint8).tobytes())
enc.stdin.close()
enc.wait()
print("wrote", OUT, f"{SECONDS}s")
