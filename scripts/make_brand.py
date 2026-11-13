"""Build the web brand assets in assets/brand/ from the official logo (raw/logo/official/logo_300dpi.png).

medallion.jpg  1024x1024 enamel face for the 3D medallion: the logo artwork on black inside a circle
logo.png       header logo (the transparent corner logo, downsized)
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets/brand"
OUT.mkdir(parents=True, exist_ok=True)

art = Image.open(ROOT / "raw/logo/official/logo_300dpi.png").convert("RGB")   # artwork on black
art = art.crop(art.getbbox())
S = 1024
face = Image.new("RGB", (S, S), (6, 6, 8))
w = int(S * 0.80)
logo = art.resize((w, round(art.height * w / art.width)), Image.LANCZOS)
face.paste(logo, ((S - logo.width) // 2, (S - logo.height) // 2))
d = ImageDraw.Draw(face)
for i, c in enumerate([(176, 130, 52), (232, 190, 98), (150, 108, 40)]):   # thin engraved gold rings
    r = S // 2 - 14 - i * 5
    d.ellipse((S // 2 - r, S // 2 - r, S // 2 + r, S // 2 + r), outline=c, width=3)
face.save(OUT / "medallion.jpg", quality=86, optimize=True)

header = Image.open(ROOT / "assets/logos/loveworld_expo_2026_logo.png").convert("RGBA")
header.thumbnail((480, 480), Image.LANCZOS)
header.save(OUT / "logo.png", optimize=True)
print("wrote", OUT / "medallion.jpg", OUT / "logo.png")
