"""Builds and runs the single ffmpeg command that assembles the final video.

Text (the engraved plaque, the watermark) is rendered with Pillow and fed in as images, so visitor
names never pass through ffmpeg filter escaping and no freetype-enabled ffmpeg build is needed.
"""
import asyncio
import json
import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config, plaque

log = logging.getLogger("ffmpeg")

W, H, FPS = 720, 1280, 24   # 24: the AI video models render 24 fps; any other rate duplicates frames (judder)
# Timeline: intro ~ A ~ (B+C, one continuous take) ~ plaque ~ end card, "~" = a XF-second dissolve.
# Each dissolve overlaps its two neighbours, so the bumpers and plaque are XF longer per dissolve they touch.
XF = 0.4
INTRO_S, CLIP_S, HOLD_S, END_S = 4.4, 5, 4.8, 3.4
TOTAL_S = round(INTRO_S + 3 * CLIP_S + HOLD_S + END_S - 4 * XF, 3)  # 25.0
A_START = INTRO_S - XF                      # 3.0: A begins dissolving in
BC_START = A_START + CLIP_S - XF            # 7.6
HOLD_START = BC_START + 2 * CLIP_S - XF     # 17.2
END_START = HOLD_START + HOLD_S - XF        # 21.6


@dataclass(frozen=True)
class Timeline:
    """Where each segment starts on the finished film, from the template's clip lengths
    (templates.json "seconds": {"A": 5, "B": 7.5, "C": 7.5}; 5 s each by default)."""
    a: float = CLIP_S
    b: float = CLIP_S
    c: float = CLIP_S
    joined: bool = False      # A, B and C are one continuous take: no dissolve between A and B

    @property
    def a_start(self) -> float:
        return round(INTRO_S - XF, 3)

    @property
    def bc_start(self) -> float:
        return round(self.a_start + self.a - (0 if self.joined else XF), 3)

    @property
    def hold_start(self) -> float:
        return round(self.bc_start + self.b + self.c - XF, 3)

    @property
    def end_start(self) -> float:
        return round(self.hold_start + HOLD_S - XF, 3)

    @property
    def total(self) -> float:
        return round(self.end_start + END_S, 3)


def timeline(template: dict | None) -> Timeline:
    secs = (template or {}).get("seconds") or {}
    joined = "A" in ((template or {}).get("continuous") or [])
    return Timeline(float(secs.get("A", CLIP_S)), float(secs.get("B", CLIP_S)), float(secs.get("C", CLIP_S)), joined)


def clip_seconds(template: dict | None, clip: str) -> float:
    return float(((template or {}).get("seconds") or {}).get(clip, CLIP_S))
SHARPEN = "unsharp=5:5:0.45:5:5:0"          # Wan output is soft; a light luma sharpen reads as "clearer"

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


def _normalize(inp: int, seconds: float, extra: str = "") -> str:
    """Any input -> exactly `seconds` of 720x1280 @FPS yuv420p (short clips hold their last frame)."""
    return (
        f"[{inp}:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,"
        f"fps={FPS},format=yuv420p,tpad=stop_mode=clone:stop_duration={seconds},"
        f"trim=end_frame={round(seconds * FPS)},setpts=PTS-STARTPTS,settb=1/{FPS}{extra}"
    )


def build_stitch_cmd(
    intro: Path, clip_a: Path, clip_b: Path, clip_c: Path, endcard: Path, music: Path,
    plaque_card: Path, plaque_focus: tuple[float, float], watermark: Path, logos: list[dict], out: Path,
    corner_logo: dict | None = None, tl: Timeline = Timeline(),
) -> list[str]:
    """logos: [{path, x, y, width}] overlaid on clip A. plaque_focus: nameplate centre in plaque_card pixels.
    corner_logo: {path, x, y, width} shown from the start of clip A to the end of the plaque."""
    hold_frames = round(HOLD_S * FPS)
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

    sharpen = f",{SHARPEN}"
    f = [
        f"{_normalize(0, INTRO_S)}[intro]",
        f"{_normalize(1, tl.a, sharpen)}[a0]",
        f"{_normalize(2, tl.b, sharpen)}[b]",
        f"{_normalize(3, tl.c, sharpen)}[c]",
        f"{_normalize(4, END_S)}[end]",
    ]
    prev = "a0"
    for i, logo in enumerate(logos):
        f.append(f"[{8 + i}:v]scale={int(logo['width'])}:-1,format=rgba[logo{i}]")
        f.append(f"[{prev}][logo{i}]overlay=x={int(logo['x'])}:y={int(logo['y'])}[a{i + 1}]")
        prev = f"a{i + 1}"
    f += [
        # B and C (and A, for a single-take template) are one continuous take: joined with a plain concat.
        (f"[{prev}][b][c]concat=n=3:v=1:a=0,settb=1/{FPS}[abc]" if tl.joined else f"[b][c]concat=n=2:v=1:a=0,settb=1/{FPS}[bc]"),
        # Plaque hold: one still frame, eased push-in from the whole plaque to the engraved name.
        f"[6:v]zoompan=z='1+{PLAQUE_ZOOM}*(1-cos(PI*on/{hold_frames - 1}))/2'"
        f":x='{fx:.0f}-iw/zoom/2':y='{fy:.0f}-ih/zoom/2':d={hold_frames}:s={W}x{H}:fps={FPS},"
        f"setsar=1,format=yuv420p,settb=1/{FPS}[hold]",
        # Dissolves between scenes (offsets are where each dissolve starts on the running timeline).
        *([f"[intro][abc]xfade=transition=fade:duration={XF}:offset={tl.a_start}[x2]"] if tl.joined else [
            f"[intro][{prev}]xfade=transition=fade:duration={XF}:offset={tl.a_start}[x1]",
            f"[x1][bc]xfade=transition=fade:duration={XF}:offset={tl.bc_start}[x2]"]),
        f"[x2][hold]xfade=transition=fade:duration={XF}:offset={tl.hold_start}[x3]",
        f"[x3][end]xfade=transition=fade:duration={XF}:offset={tl.end_start}[cat]",
        f"[cat]{GRADE},{GRAIN}[graded]",
        "[graded][7:v]overlay=0:0[marked]",
        _corner(corner_logo, 8 + len(logos), tl),
        f"[5:a]atrim=0:{tl.total},asetpts=PTS-STARTPTS,afade=t=out:st={tl.total - 1}:d=1,"
        "aformat=sample_rates=48000:channel_layouts=stereo[aout]",
    ]
    cmd += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "4M", "-bufsize", "8M",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-t", str(tl.total), "-movflags", "+faststart", str(out)]
    return cmd


def clip_groups(template: dict) -> list[list[str]]:
    """Clips Wan processes together. templates.json "continuous": ["B", "C"] marks one continuous take:
    swapped as ONE Wan job so the visitor's invented body/outfit can't change half-way through the scene."""
    cont = list(template.get("continuous") or [])
    groups, seen = [], set()
    for c in config.CLIPS:
        if c not in seen:
            g = cont if c in cont else [c]
            groups.append(g)
            seen.update(g)
    return groups


def group_of(template: dict, clip: str) -> list[str]:
    return next(g for g in clip_groups(template) if clip in g)


def concat_cmd(sources: list[Path], out: Path) -> list[str]:
    n = len(sources)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for src in sources:
        cmd += ["-i", str(src)]
    chain = "".join(f"[{i}:v]" for i in range(n))
    return cmd + ["-filter_complex", f"{chain}concat=n={n}:v=1:a=0[v]", "-map", "[v]", "-an",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]


def split_cmd(src: Path, start: float, seconds: float, out: Path) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-vf", f"trim=start={start}:duration={seconds},setpts=PTS-STARTPTS", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]


def swap_region(template: dict, clip: str) -> dict | None:
    """templates.json "swap_regions": {"C": {"x", "width", "feather"}} for clips with a second person.

    Wan's replace mode can't be told which person to swap; with two people in frame it may swap neither
    (seen on clip C). So only the region holding the stand-in is sent to Wan, and the result is blended
    back over the untouched template. Everyone outside the region (Pastor Chris) is never regenerated.
    """
    return (template.get("swap_regions") or {}).get(clip)


def retime_cmd(src: Path, src_seconds: float, seconds: float, out: Path) -> list[str]:
    """Put a swap engine's output on the template's clock: FPS, exactly `seconds` long.

    The blend pairs template frame N with swap frame N, so both must mean the same moment. Wan returns 30 fps,
    Seedance returns 24 fps and a few frames short (353 for a 15 s take); pairing those by index ran the visitor
    27% ahead of Pastor Chris and froze them at the end. Stretching by the duration ratio (~2%) keeps the
    engine's motion on the template's timeline from first frame to last.
    """
    stretch = seconds / src_seconds if src_seconds > 0 else 1.0
    frames = round(seconds * FPS)
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-vf", f"setpts=PTS*{stretch:.6f},fps={FPS},scale={W}:{H},setsar=1", "-frames:v", str(frames),
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", str(out)]


def swap_input_cmd(src: Path, region: dict, out: Path) -> list[str]:
    """The template with everything outside the region smeared into person-free colour streaks. A moving region
    is shown at its widest (the union of its keyframes); the blend then follows it frame by frame."""
    from .composite import region_bounds
    x, w = region_bounds(region)
    right = W - x - w
    fill = f"fillborders=left={x}:right={right}:mode=smear" if (x or right) else "null"
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-vf", f"crop={w}:{H}:{x}:0,pad={W}:{H}:{x}:0,{fill},setsar=1",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]


def _corner(corner_logo: dict | None, index: int, tl: Timeline = Timeline()) -> str:
    """Overlay the corner logo on clips A-C (the intro, plaque and end card show the brand already)."""
    if not corner_logo:
        return "[marked]format=yuv420p[v]"
    start, end = tl.a_start + XF / 2, tl.hold_start  # A, B, C only: the plaque carries the logo itself
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
    tl = timeline(config.load_templates().get(job["template_id"]))
    cmd = build_stitch_cmd(
        a / "intro.mp4", clip_paths["A"], clip_paths["B"], clip_paths["C"], a / "endcard.mp4", a / "music.mp3",
        plaque_card, plaque_focus, watermark, logos, tmp,
        corner_logo=({**corner, "path": a / corner["file"]} if (corner := config.load_branding().get("corner_logo")) else None),
        tl=tl,
    )
    await run(cmd, job["id"])
    # Poster = the "Honouring {name}" moment; shown before playback and as the link-preview image.
    poster_s = tl.end_start - 0.3  # the plaque, fully pushed in on the name
    await run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(poster_s), "-i", str(tmp),
               "-frames:v", "1", "-q:v", "3", str(job_dir / "poster.jpg")], job["id"])
    os.replace(tmp, out)
    return out
