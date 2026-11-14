"""Validate and prepare the visitor photo. Pure CPU work — call via asyncio.to_thread."""
import io
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

OUT_W, OUT_H = 720, 1280
_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


class PhotoRejected(Exception):
    """Message is shown to the operator verbatim, so it says what to do."""


@dataclass
class PhotoInfo:
    sharpness: float
    faces: int
    width: int
    height: int


def check_and_prepare(data: bytes, original_out: Path, prepared_out: Path, face_out: Path | None = None) -> PhotoInfo:
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise PhotoRejected("That file isn't a photo we can read. Please retake the photo.")

    w, h = img.size
    if min(w, h) < config.MIN_PHOTO_SIDE:
        raise PhotoRejected(f"Photo is too small ({w}x{h}). Use the main camera and retake.")
    if w > h * 1.1:
        raise PhotoRejected("Photo is landscape. Hold the phone upright and retake (full body, head to feet).")

    # Analyse at a fixed size so thresholds mean the same thing for every camera.
    scale = 1280 / max(w, h)
    small = img.resize((round(w * scale), round(h * scale)))
    gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharpness < config.BLUR_THRESHOLD:
        raise PhotoRejected(
            f"Photo is blurry (sharpness {sharpness:.0f}, need {config.BLUR_THRESHOLD:.0f}). "
            "Hold still, tap to focus and retake."
        )

    faces, _, weights = _cascade.detectMultiScale3(gray, scaleFactor=1.08, minNeighbors=5, minSize=(28, 28),
                                                   outputRejectLevels=True)
    if config.REQUIRE_FACE and len(faces) == 0:
        raise PhotoRejected("No face found. Visitor should face the camera with good light on their face; retake.")

    # Centre the 9:16 crop on the largest face so the person isn't cut off.
    face_cx = None
    if len(faces):
        # The most confident detection, not the largest: Haar's biggest box can be a false hit (trousers, a
        # pattern); the real face scores far higher (7.1 vs 0.7 on the test photo).
        fx, fy, fw, fh = faces[int(np.argmax(np.ravel(weights)))]
        face_cx = (fx + fw / 2) / scale
        # The film shows the whole body, so the photo must too. A standing adult's head is about 1/8 of their
        # height; in a selfie or head-and-shoulders shot the face fills far more of the frame (a selfie
        # measured 0.44 vs 0.05-0.09 for full-length shots). Without the body, Wan invents one per clip.
        ratio = fh / gray.shape[0]
        if ratio > config.MAX_FACE_RATIO:
            raise PhotoRejected(
                "This is a close-up. Step back about 3 metres and retake: we need the whole person, head to feet.")

    original_out.write_bytes(data)
    _to_portrait(img, face_cx).save(prepared_out, "JPEG", quality=92)
    if face_out is not None and len(faces):
        # Full-resolution face crop for the face-identity pass: the face box from the analysis copy, scaled back.
        m = max(fw, fh) / scale * 0.95
        cx, cy = face_cx, (fy + fh / 2) / scale
        img.crop((int(max(0, cx - m)), int(max(0, cy - m)), int(min(w, cx + m)), int(min(h, cy + m)))).save(
            face_out, "JPEG", quality=95)
    return PhotoInfo(sharpness=round(sharpness, 1), faces=len(faces), width=w, height=h)


def _to_portrait(img: Image.Image, face_cx: float | None) -> Image.Image:
    """Crop (if wider) or pad (if taller) to 9:16, then resize to 720x1280."""
    w, h = img.size
    target = OUT_W / OUT_H
    if w / h > target:
        new_w = round(h * target)
        cx = face_cx if face_cx is not None else w / 2
        left = int(min(max(cx - new_w / 2, 0), w - new_w))
        img = img.crop((left, 0, left + new_w, h))
    elif w / h < target:
        new_w = round(h * target)
        border = np.asarray(img)[:, [0, -1]].reshape(-1, 3).mean(axis=0)
        canvas = Image.new("RGB", (new_w, h), tuple(int(c) for c in border))
        canvas.paste(img, ((new_w - w) // 2, 0))
        img = canvas
    return img.resize((OUT_W, OUT_H), Image.LANCZOS)
