"""Generate placeholder assets so the full pipeline runs before real assets arrive.

Never overwrites an existing file. Usage: python scripts/make_placeholder_assets.py [assets_dir]
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets")
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def video(path: Path, seconds: int, source: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-f", "lavfi", "-i", f"{source}{':' if '=' in source else '='}size=720x1280:rate=30:duration={seconds}",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path))
    print("created", path)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    templates = json.loads((ASSETS / "templates.json").read_text())
    hues = {"A": "testsrc2", "B": "smptehdbars", "C": "rgbtestsrc"}
    for t in templates:
        for clip, src in hues.items():
            video(ASSETS / t["folder"] / f"{clip}.mp4", 5, src)
    video(ASSETS / "intro.mp4", 3, "color=c=0x1d2a5c")
    video(ASSETS / "endcard.mp4", 3, "color=c=0x5c1d2a")

    music = ASSETS / "music.mp3"
    if not music.exists():
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-c:a", "libmp3lame", "-q:a", "5", str(music))
        print("created", music)

    font = ASSETS / "font.ttf"
    if not font.exists():
        src = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
        if not src:
            sys.exit("No system font found; copy any .ttf to assets/font.ttf")
        shutil.copy(src, font)
        print("created", font, "from", src)

    logo = ASSETS / "logos" / "placeholder_logo.png"
    if not logo.exists():
        logo.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (400, 160), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((4, 4, 396, 156), radius=24, outline=(255, 255, 255, 230), width=8)
        d.text((40, 60), "LOGO PLACEHOLDER", fill=(255, 255, 255, 230))
        img.save(logo)
        print("created", logo)

    positions = ASSETS / "logo_positions.json"
    if not positions.exists():
        positions.write_text(json.dumps(
            {t["id"]: [{"file": "placeholder_logo.png", "x": 40, "y": 60, "width": 220}] for t in templates}, indent=2
        ) + "\n")
        print("created", positions)


if __name__ == "__main__":
    main()
