"""Blend Wan's output back into the template using the visitor's silhouette, not a straight strip.

Wan only sees the swap region (the stand-in's side of the frame) and redraws it. Inside that region it
changes the person a lot and everything else very little. So the pixels that changed are the visitor:
take those from Wan and every other pixel from the untouched template. Where two people touch (the
handshake, the plaque hand-over) Pastor Chris's hand therefore stays his original pixels instead of a
half-redrawn double image along a fixed seam.

Streams frame by frame (two decoders in, one encoder out), so memory stays flat. CPU only, ~1 s per 100 frames.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np

W, H = 720, 1280
DIFF_THRESHOLD = 22      # mean abs RGB difference (blurred) that counts as "Wan changed this pixel"
MIN_BLOB = 2500          # px; smaller changed areas are Wan re-rendering noise, not the person
EDGE_FADE = 24           # px; the mask fades out before the region edge, so nothing is cut by a straight line
TEMPORAL = 0.6           # keep 60% of last frame's mask: no flicker where a frame's difference dips


def _frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            if frame.shape[1] != W or frame.shape[0] != H:
                frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_LANCZOS4)
            yield frame
    finally:
        cap.release()


def region_at(region: dict, t: float) -> tuple[int, int]:
    """(x, width) of the swap region at time t. A region is either fixed {"x", "width"} or moves with the
    visitor: {"keys": [{"t": 0, "x": 0, "width": 480}, {"t": 7.5, "x": 0, "width": 350}]}, linear between keys."""
    keys = region.get("keys")
    if not keys:
        return int(region["x"]), int(region["width"])
    ts = [k["t"] for k in keys]
    x = float(np.interp(t, ts, [k["x"] for k in keys]))
    x1 = float(np.interp(t, ts, [k["x"] + k["width"] for k in keys]))
    return int(round(x)), int(round(x1 - x))


def region_bounds(region: dict) -> tuple[int, int]:
    """(x, width) of the union of every position the region takes: what the swap engine is shown."""
    keys = region.get("keys") or [region]
    x0 = min(int(k["x"]) for k in keys)
    x1 = max(int(k["x"]) + int(k["width"]) for k in keys)
    return x0, x1 - x0


def _ramp(x0: int, rw: int, ax: int, aw: int) -> np.ndarray:
    """Weight per column of the strip [x0, x0+rw): 1 inside the active region [ax, ax+aw), fading to 0 over
    EDGE_FADE px at any active edge that isn't the frame edge."""
    cols = np.arange(x0, x0 + rw)
    ramp = np.ones(rw, np.float32)
    if ax + aw < W:
        ramp = np.minimum(ramp, np.clip((ax + aw - cols) / EDGE_FADE, 0, 1))
    if ax > 0:
        ramp = np.minimum(ramp, np.clip((cols - ax + 1) / EDGE_FADE, 0, 1))
    return ramp[None, :, None]


def silhouette_composite(template: Path, wan: Path, region: dict, out: Path, fps: int = 24) -> int:
    """Write `out` (template-length H.264) and return the number of frames written."""
    x0, rw = region_bounds(region)
    x1 = x0 + rw
    k_open, k_close, k_grow = (np.ones((s, s), np.uint8) for s in (5, 21, 9))

    enc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "16", "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    wan_iter = _frames(wan)
    last_wan, prev, n = None, None, 0
    try:
        for t in _frames(template):
            w = next(wan_iter, None)
            if w is None:            # Wan's output can be a frame or two short: hold its last frame
                w = last_wan if last_wan is not None else t
            last_wan = w
            tr = t[:, x0:x1].astype(np.float32)
            wr = w[:, x0:x1].astype(np.float32)
            diff = cv2.GaussianBlur(np.abs(wr - tr).mean(axis=2), (0, 0), 3)
            m = (diff > DIFF_THRESHOLD).astype(np.uint8)
            m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k_open), cv2.MORPH_CLOSE, k_close)
            count, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
            keep = np.zeros_like(m)
            for c in range(1, count):
                if stats[c, cv2.CC_STAT_AREA] > MIN_BLOB:
                    keep[labels == c] = 1
            m = cv2.dilate(keep, k_grow).astype(np.float32)
            if prev is not None:
                m = np.maximum(m, prev * TEMPORAL)
            prev = m
            alpha = cv2.GaussianBlur(m, (0, 0), 4)[..., None] * _ramp(x0, rw, *region_at(region, n / fps))
            frame = t.copy()
            frame[:, x0:x1] = (alpha * wr + (1 - alpha) * tr).astype(np.uint8)
            enc.stdin.write(frame.tobytes())
            n += 1
    finally:
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")
        enc.wait()
    if enc.returncode != 0 or n == 0:
        raise RuntimeError(f"silhouette composite failed ({n} frames): {err[-500:]}")
    return n
