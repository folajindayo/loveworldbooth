"""Draw a crude full-body figure with a face OpenCV's Haar detector recognises. For tests only."""
import sys

import cv2
import numpy as np


def make(path: str) -> None:
    img = np.full((1280, 720, 3), (200, 205, 210), np.uint8)
    img = cv2.add(img, np.random.default_rng(1).integers(0, 40, img.shape, dtype=np.uint8))
    cx, cy = 360, 260
    cv2.ellipse(img, (cx, cy), (85, 110), 0, 0, 360, (120, 160, 205), -1)
    cv2.ellipse(img, (cx, cy - 95), (90, 45), 0, 180, 360, (30, 30, 40), -1)
    for dx in (-35, 35):
        cv2.ellipse(img, (cx + dx, cy - 30), (24, 8), 0, 0, 360, (60, 60, 70), -1)
        cv2.ellipse(img, (cx + dx, cy - 12), (18, 9), 0, 0, 360, (250, 250, 250), -1)
        cv2.circle(img, (cx + dx, cy - 12), 7, (20, 20, 20), -1)
    cv2.line(img, (cx, cy - 5), (cx - 8, cy + 35), (90, 120, 160), 4)
    cv2.ellipse(img, (cx, cy + 60), (35, 12), 0, 0, 180, (60, 60, 140), -1)
    cv2.rectangle(img, (250, 380), (470, 900), (90, 60, 40), -1)
    cv2.rectangle(img, (270, 900), (350, 1220), (40, 40, 40), -1)
    cv2.rectangle(img, (370, 900), (450, 1220), (40, 40, 40), -1)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 92])


if __name__ == "__main__":
    make(sys.argv[1])
