"""Build assets/endcard.mp4 (3 s, 720x1280, 30 fps, no audio): the logo over the blurred Expo hall,
a slow push-in, and a fade to black under the music fade-out.

Usage: .venv/bin/python scripts/make_endcard.py
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
BG = ROOT / "raw/reference/expo_hall.png"
LOGO = ROOT / "assets/logos/loveworld_expo_2026_logo.png"
W, H, S = 720, 1280, 2  # render at 2x so the push-in stays sharp

bg = Image.open(BG).convert("RGB")
scale = max(W * S / bg.width, H * S / bg.height)
bg = bg.resize((round(bg.width * scale), round(bg.height * scale)), Image.LANCZOS)
left, top = (bg.width - W * S) // 2, (bg.height - H * S) // 2
bg = bg.crop((left, top, left + W * S, top + H * S)).filter(ImageFilter.GaussianBlur(36))
bg = ImageEnhance.Brightness(bg).enhance(0.32).convert("RGBA")

logo = Image.open(LOGO).convert("RGBA")
lw = int(W * S * 0.80)
logo = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
bg.alpha_composite(logo, ((W * S - lw) // 2, int(H * S * 0.44 - logo.height / 2)))
frame = ROOT / "raw/generated/endcard_frame.png"
frame.parent.mkdir(parents=True, exist_ok=True)
bg.convert("RGB").save(frame)

subprocess.run([
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(frame),
    "-vf", f"zoompan=z='1+0.05*on/89':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=90:s={W}x{H}:fps=30,"
           "fade=t=in:st=0:d=0.3,fade=t=out:st=2.4:d=0.6,setsar=1,format=yuv420p",
    "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-t", "3", str(ROOT / "assets/endcard.mp4"),
], check=True)
print("wrote assets/endcard.mp4")
