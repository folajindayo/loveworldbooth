"""Builds and runs the single ffmpeg command that assembles the final video.

Text (the engraved plaque, the watermark) is rendered with Pillow and fed in as images, so visitor
names never pass through ffmpeg filter escaping and no freetype-enabled ffmpeg build is needed.
"""
import asyncio
import json
import logging
import os
import shlex
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config, plaque

log = logging.getLogger("ffmpeg")

W, H, FPS = 720, 1280, 30
# (segment, seconds) in order: intro, clips A/B/C, plaque hold (the visitor's engraved name), end card.
INTRO_S, CLIP_S, HOLD_S, END_S = 3, 5, 4, 3
TOTAL_S = INTRO_S + 3 * CLIP_S + HOLD_S + END_S  # 25

GRADE = "eq=contrast=1.06:saturation=1.10:brightness=0.01:gamma=0.98"
PLAQUE_ZOOM = 0.6  # the hold eases from the whole plaque to 1.6x on the nameplate
# Per-frame grain is incompressible: alls=6 made a 25 s file ~41 MB, alls=4 + the bitrate cap ~4 MB.
GRAIN = "noise=alls=4:allf=t"


class FFmpegError(Exception):
    pass


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(config.ASSETS_DIR / "font.ttf"), size)


def render_watermark(out: Path) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _font(22)
    text = config.WATERMARK_TEXT
    x = W - font.getlength(text) - 24
    y = H - 22 - 28
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 110))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 180))
    img.save(out)


def _normalize(inp: int, seconds: int) -> str:
    """Any input -> exactly `seconds` of 720x1280 @30fps yuv420p (short clips hold their last frame)."""
    return (
        f"[{inp}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
        f"fps={FPS},format=yuv420p,tpad=stop_mode=clone:stop_duration={seconds},"
        f"trim=end_frame={seconds * FPS},setpts=PTS-STARTPTS"
    )


def build_stitch_cmd(
    intro: Path, clip_a: Path, clip_b: Path, clip_c: Path, endcard: Path, music: Path,
    plaque_card: Path, plaque_focus: tuple[float, float], watermark: Path, logos: list[dict], out: Path,
    corner_logo: dict | None = None,
) -> list[str]:
    """logos: [{path, x, y, width}] overlaid on clip A. plaque_focus: nameplate centre in plaque_card pixels.
    corner_logo: {path, x, y, width} shown from the start of clip A to the end of the plaque."""
    hold_frames = HOLD_S * FPS
    fx, fy = plaque_focus
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(intro), "-i", str(clip_a), "-i", str(clip_b), "-i", str(clip_c), "-i", str(endcard),
           "-stream_loop", "-1", "-i", str(music),
           "-i", str(plaque_card),
           "-i", str(watermark)]
    for logo in logos:
        cmd += ["-i", str(logo["path"])]
    if corner_logo:
        cmd += ["-i", str(corner_logo["path"])]

    f = [
        f"{_normalize(0, INTRO_S)}[intro]",
        f"{_normalize(1, CLIP_S)}[a0]",
        f"{_normalize(2, CLIP_S)}[b]",
        f"{_normalize(3, CLIP_S)}[c]",
        f"{_normalize(4, END_S)}[end]",
    ]
    prev = "a0"
    for i, logo in enumerate(logos):
        f.append(f"[{8 + i}:v]scale={int(logo['width'])}:-1,format=rgba[logo{i}]")
        f.append(f"[{prev}][logo{i}]overlay=x={int(logo['x'])}:y={int(logo['y'])}[a{i + 1}]")
        prev = f"a{i + 1}"
    f += [
        # Plaque hold: one still frame, eased push-in from the whole plaque to the engraved name.
        f"[6:v]zoompan=z='1+{PLAQUE_ZOOM}*(1-cos(PI*on/{hold_frames - 1}))/2'"
        f":x='{fx:.0f}-iw/zoom/2':y='{fy:.0f}-ih/zoom/2':d={hold_frames}:s={W}x{H}:fps={FPS},"
        "setsar=1,format=yuv420p,fade=t=in:st=0:d=0.35[hold]",
        f"[intro][{prev}][b][c][hold][end]concat=n=6:v=1:a=0[cat]",
        f"[cat]{GRADE},{GRAIN}[graded]",
        "[graded][7:v]overlay=0:0[marked]",
        _corner(corner_logo, 8 + len(logos)),
        f"[5:a]atrim=0:{TOTAL_S},asetpts=PTS-STARTPTS,afade=t=out:st={TOTAL_S - 1}:d=1,"
        "aformat=sample_rates=48000:channel_layouts=stereo[aout]",
    ]
    cmd += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "4M", "-bufsize", "8M",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-t", str(TOTAL_S), "-movflags", "+faststart", str(out)]
    return cmd


def swap_region(template: dict, clip: str) -> dict | None:
    """templates.json "swap_regions": {"C": {"x", "width", "feather"}} for clips with a second person.

    Wan's replace mode can't be told which person to swap; with two people in frame it may swap neither
    (seen on clip C). So only the region holding the stand-in is sent to Wan, and the result is blended
    back over the untouched template. Everyone outside the region (Pastor Chris) is never regenerated.
    """
    return (template.get("swap_regions") or {}).get(clip)


def swap_input_cmd(src: Path, region: dict, out: Path) -> list[str]:
    """The template with everything outside the region smeared into person-free colour streaks."""
    x, w = int(region["x"]), int(region["width"])
    right = W - x - w
    fill = f"fillborders=left={x}:right={right}:mode=smear" if (x or right) else "null"
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-vf", f"crop={w}:{H}:{x}:0,pad={W}:{H}:{x}:0,{fill},setsar=1",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]


def region_mask(region: dict, out: Path) -> None:
    """White inside the region, fading to black over `feather` px at edges that border the rest of the frame."""
    x, w, f = int(region["x"]), int(region["width"]), int(region.get("feather", 40))
    mask = Image.new("L", (W, H), 0)
    for col in range(x, x + w):
        v = 255
        if x > 0 and col < x + f:
            v = min(v, int(255 * (col - x) / f))
        if x + w < W and col >= x + w - f:
            v = min(v, int(255 * (x + w - col) / f))
        mask.paste(v, (col, 0, col + 1, H))
    mask.save(out)


def composite_cmd(template_clip: Path, swapped: Path, mask: Path, out: Path) -> list[str]:
    """Blend Wan's output into the template through the region mask (template pixels everywhere else)."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(template_clip), "-i", str(swapped), "-loop", "1", "-i", str(mask),
            "-filter_complex",
            f"[0:v]scale={W}:{H},fps={FPS},setsar=1,format=yuv420p[t];"
            f"[1:v]scale={W}:{H},fps={FPS},setsar=1,format=yuva420p[s];"
            f"[2:v]scale={W}:{H},format=gray[m];[s][m]alphamerge[sa];"
            "[t][sa]overlay=0:0:shortest=1,format=yuv420p[v]",
            "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
            str(out)]


def _corner(corner_logo: dict | None, index: int) -> str:
    """Overlay the corner logo on clips A-C (the intro, plaque and end card show the brand already)."""
    if not corner_logo:
        return "[marked]format=yuv420p[v]"
    start, end = INTRO_S, INTRO_S + 3 * CLIP_S  # A, B, C only: the plaque carries the logo itself
    return (f"[{index}:v]scale={int(corner_logo['width'])}:-1,format=rgba[corner];"
            f"[marked][corner]overlay=x={int(corner_logo['x'])}:y={int(corner_logo['y'])}"
            f":enable='between(t,{start},{end})',format=yuv420p[v]")


async def run(cmd: list[str], job_id: int | None = None) -> None:
    log.info("job=%s ffmpeg: %s", job_id, shlex.join(cmd))
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-1500:]
        raise FFmpegError(f"ffmpeg exited {proc.returncode}: {tail}")


async def probe(path: Path) -> dict:
    """-> {duration, width, height, has_audio}. Raises FFmpegError if the file isn't a readable video."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path.name}: {err.decode(errors='replace')[-500:]}")
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise FFmpegError(f"{path.name} has no video stream")
    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "width": video.get("width"),
        "height": video.get("height"),
        "has_audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
    }


async def stitch(job: dict, clip_paths: dict[str, Path], job_dir: Path) -> Path:
    """Render the final video for a job. Writes to a temp file then renames, so a crash never leaves a half file."""
    plaque_card = job_dir / "plaque_card.png"
    plaque_focus = plaque.render_card(job["name"], plaque_card)
    watermark = config.DATA_DIR / "watermark.png"
    if not watermark.exists():
        render_watermark(watermark)
    logos = [
        {**l, "path": config.ASSETS_DIR / "logos" / l["file"]} for l in config.load_logo_positions(job["template_id"])
    ]
    out = job_dir / "final.mp4"
    tmp = job_dir / "final.tmp.mp4"
    a = config.ASSETS_DIR
    cmd = build_stitch_cmd(
        a / "intro.mp4", clip_paths["A"], clip_paths["B"], clip_paths["C"], a / "endcard.mp4", a / "music.mp3",
        plaque_card, plaque_focus, watermark, logos, tmp,
        corner_logo=({**corner, "path": a / corner["file"]} if (corner := config.load_branding().get("corner_logo")) else None),
    )
    await run(cmd, job["id"])
    # Poster = the "Honouring {name}" moment; shown before playback and as the link-preview image.
    poster_s = INTRO_S + 3 * CLIP_S + HOLD_S - 0.5
    await run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(poster_s), "-i", str(tmp),
               "-frames:v", "1", "-q:v", "3", str(job_dir / "poster.jpg")], job["id"])
    os.replace(tmp, out)
    return out
