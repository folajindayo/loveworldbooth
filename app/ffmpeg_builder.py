"""Builds and runs the single ffmpeg command that assembles the final video.

Text (name card, watermark) is rendered to transparent PNGs with Pillow and overlaid, so visitor
names never pass through ffmpeg filter escaping and no freetype-enabled ffmpeg build is needed.
"""
import asyncio
import json
import logging
import os
import shlex
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

log = logging.getLogger("ffmpeg")

W, H, FPS = 720, 1280, 30
# (segment, seconds) — the timeline from the spec, in order.
INTRO_S, CLIP_S, HOLD_S, END_S = 3, 5, 4, 3
TOTAL_S = INTRO_S + 3 * CLIP_S + HOLD_S + END_S  # 25

GRADE = "eq=contrast=1.06:saturation=1.10:brightness=0.01:gamma=0.98"
# Per-frame grain is incompressible: alls=6 made a 25 s file ~41 MB, alls=4 + the bitrate cap ~4 MB.
GRAIN = "noise=alls=4:allf=t"


class FFmpegError(Exception):
    pass


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(config.ASSETS_DIR / "font.ttf"), size)


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, y: int, size: int, max_width: int) -> int:
    """Draw text centered at y, shrinking the font until it fits. Returns the line height used."""
    while size > 18 and _font(size).getlength(text) > max_width:
        size -= 2
    font = _font(size)
    x = (W - font.getlength(text)) / 2
    draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 150))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    return int(size * 1.25)


def render_name_card(name: str, out: Path) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = int(H * 0.70)
    y += _draw_centered(draw, "Honouring", y, 44, W - 80)
    _draw_centered(draw, name, y, 76, W - 80)
    img.save(out)


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
    name_card: Path, watermark: Path, logos: list[dict], out: Path,
) -> list[str]:
    """logos: [{path, x, y, width}] overlaid on clip A."""
    hold_frames = HOLD_S * FPS
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(intro), "-i", str(clip_a), "-i", str(clip_b), "-i", str(clip_c), "-i", str(endcard),
           "-stream_loop", "-1", "-i", str(music),
           "-loop", "1", "-framerate", str(FPS), "-t", str(HOLD_S), "-i", str(name_card),
           "-i", str(watermark)]
    for logo in logos:
        cmd += ["-i", str(logo["path"])]

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
        "[c]split[c1][c2]",
        # Victory hold: last frame of C, upscaled first so the slow push-in doesn't jitter.
        f"[c2]trim=start_frame={CLIP_S * FPS - 1},setpts=PTS-STARTPTS,scale={W * 2}:{H * 2},"
        f"zoompan=z='1+0.08*on/{hold_frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={hold_frames}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p[hold0]",
        "[6:v]format=rgba,fade=t=in:st=0.3:d=0.7:alpha=1[name]",
        "[hold0][name]overlay=0:0:shortest=1,format=yuv420p[hold]",
        f"[intro][{prev}][b][c1][hold][end]concat=n=6:v=1:a=0[cat]",
        f"[cat]{GRADE},{GRAIN}[graded]",
        "[graded][7:v]overlay=0:0,format=yuv420p[v]",
        f"[5:a]atrim=0:{TOTAL_S},asetpts=PTS-STARTPTS,afade=t=out:st={TOTAL_S - 1}:d=1,"
        "aformat=sample_rates=48000:channel_layouts=stereo[aout]",
    ]
    cmd += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "4M", "-bufsize", "8M",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-t", str(TOTAL_S), "-movflags", "+faststart", str(out)]
    return cmd


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
    name_card = job_dir / "name_card.png"
    render_name_card(job["name"], name_card)
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
        name_card, watermark, logos, tmp,
    )
    await run(cmd, job["id"])
    # Poster = the "Honouring {name}" moment; shown before playback and as the link-preview image.
    poster_s = INTRO_S + 3 * CLIP_S + HOLD_S - 0.5
    await run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(poster_s), "-i", str(tmp),
               "-frames:v", "1", "-q:v", "3", str(job_dir / "poster.jpg")], job["id"])
    os.replace(tmp, out)
    return out
