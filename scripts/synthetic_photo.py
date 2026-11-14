"""Draw a crude full-length figure (realistic proportions: head ~1/8 of the body) whose face OpenCV's Haar
detector recognises. For tests only."""
import sys

import cv2
import numpy as np


def make(path: str) -> None:
    img = np.full((1280, 720, 3), (200, 205, 210), np.uint8)
    img = cv2.add(img, np.random.default_rng(1).integers(0, 40, img.shape, dtype=np.uint8))
    cx, cy, k = 360, 210, 0.62   # face centre and scale relative to the original drawing
    s = lambda v: int(v * k)
    cv2.ellipse(img, (cx, cy), (s(85), s(110)), 0, 0, 360, (120, 160, 205), -1)
    cv2.ellipse(img, (cx, cy - s(95)), (s(90), s(45)), 0, 180, 360, (30, 30, 40), -1)
    for dx in (-35, 35):
        cv2.ellipse(img, (cx + s(dx), cy - s(30)), (s(24), s(8)), 0, 0, 360, (60, 60, 70), -1)
        cv2.ellipse(img, (cx + s(dx), cy - s(12)), (s(18), s(9)), 0, 0, 360, (250, 250, 250), -1)
        cv2.circle(img, (cx + s(dx), cy - s(12)), max(2, s(7)), (20, 20, 20), -1)
    cv2.line(img, (cx, cy - s(5)), (cx - s(8), cy + s(35)), (90, 120, 160), 3)
    cv2.ellipse(img, (cx, cy + s(60)), (s(35), s(12)), 0, 0, 180, (60, 60, 140), -1)
    cv2.rectangle(img, (275, 290), (445, 760), (90, 60, 40), -1)              # torso
    cv2.rectangle(img, (240, 300), (272, 640), (90, 60, 40), -1)              # arms
    cv2.rectangle(img, (448, 300), (480, 640), (90, 60, 40), -1)
    cv2.rectangle(img, (290, 760), (352, 1180), (40, 40, 40), -1)             # legs
    cv2.rectangle(img, (368, 760), (430, 1180), (40, 40, 40), -1)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 92])


if __name__ == "__main__":
    make(sys.argv[1])
