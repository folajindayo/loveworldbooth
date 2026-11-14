"""All settings come from the environment (.env). Read once at import."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY", "").strip()
WAVESPEED_API_BASE = os.getenv("WAVESPEED_API_BASE", "https://api.wavespeed.ai/api/v3").rstrip("/")
WAN_RESOLUTION = os.getenv("WAN_RESOLUTION", "720p")
WAN_PROMPT = os.getenv("WAN_PROMPT", "")
WAN_TIMEOUT_S = _int("WAN_TIMEOUT_S", 1200)
WAN_POLL_S = _int("WAN_POLL_S", 5)
WAN_MAX_ATTEMPTS = 2  # first try + 1 automatic retry

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
OPERATOR_PIN = os.getenv("OPERATOR_PIN", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
SESSION_HOURS = _int("SESSION_HOURS", 16)

MAX_CONCURRENT_WAN_JOBS = _int("MAX_CONCURRENT_WAN_JOBS", 6)
MAX_CONCURRENT_FFMPEG = _int("MAX_CONCURRENT_FFMPEG", 4)

MOCK_WAN = _bool("MOCK_WAN", False)
# Second pass after Wan: a dedicated face-swap model puts the visitor's own face (hi-res crop from their photo)
# onto every frame. Wan carries body, clothes and motion; this carries facial likeness.
FACE_SWAP = _bool("FACE_SWAP", True)
# Per-visitor swap engine. "seedance": Seedance 2.5 video-edit (Turbo) re-renders the person from the visitor's own
# photos (face, hair, glasses, outfit) — measured clearly closer likeness than Wan, ~2.4x the cost.
# "wan": Wan 2.2 Animate replace (+ the optional FACE_SWAP pass).
SWAP_ENGINE = os.getenv("SWAP_ENGINE", "seedance").strip().lower()
SEEDANCE_EDIT_MODEL = os.getenv("SEEDANCE_EDIT_MODEL", "bytedance/seedance-2.5/video-edit-turbo")
SEEDANCE_EDIT_PROMPT = os.getenv("SEEDANCE_EDIT_PROMPT", (
    "Replace the main person in this video with the person shown in @Image 1 and @Image 2: the same face, the same "
    "hairstyle, the same glasses or accessories, the same outfit and the same build. Keep everything else exactly as it "
    "is in the video: their movements and timing, the camera movement, the background and the lighting. Change nothing else."))
FACE_SWAP_MODEL = os.getenv("FACE_SWAP_MODEL", "wavespeed-ai/video-face-swap")
MOCK_WAN_DELAY_S = float(os.getenv("MOCK_WAN_DELAY_S", "3"))
MOCK_WAN_FAIL_ONCE = {c.strip().upper() for c in os.getenv("MOCK_WAN_FAIL_ONCE", "").split(",") if c.strip()}

BLUR_THRESHOLD = float(os.getenv("BLUR_THRESHOLD", "40"))
MIN_PHOTO_SIDE = _int("MIN_PHOTO_SIDE", 480)
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 15)
REQUIRE_FACE = _bool("REQUIRE_FACE", True)
MAX_FACE_RATIO = float(os.getenv("MAX_FACE_RATIO", "0.16"))  # face height / photo height; above = not full length

DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", str(ROOT / "assets"))).resolve()
DB_PATH = DATA_DIR / "app.db"
JOBS_DIR = DATA_DIR / "jobs"

CLIPS = ("A", "B", "C")
WATERMARK_TEXT = "A LoveWorld Expo Moment"


def load_templates() -> dict:
    """templates.json: [{id, label, folder}] -> {id: {...}}. Adding a template is a JSON edit."""
    items = json.loads((ASSETS_DIR / "templates.json").read_text())
    return {t["id"]: t for t in items}


def template_clip(template: dict, clip: str) -> Path:
    return ASSETS_DIR / template["folder"] / f"{clip}.mp4"


def load_logo_positions(template_id: str) -> list[dict]:
    path = ASSETS_DIR / "logo_positions.json"
    if not path.exists():
        return []
    return json.loads(path.read_text()).get(template_id, [])


def load_branding() -> dict:
    """assets/branding.json: {"corner_logo": {file, x, y, width}} shown from clip A through the plaque."""
    path = ASSETS_DIR / "branding.json"
    return json.loads(path.read_text()) if path.exists() else {}


def validate() -> list[str]:
    """Return human-readable problems; the app refuses to start if any exist."""
    problems = []
    if not OPERATOR_PIN:
        problems.append("OPERATOR_PIN is not set")
    if not MOCK_WAN and not WAVESPEED_API_KEY:
        problems.append("WAVESPEED_API_KEY is not set (or set MOCK_WAN=true)")
    for name in ("intro.mp4", "endcard.mp4", "music.mp3", "font.ttf", "templates.json", "plaque.json"):
        if not (ASSETS_DIR / name).exists():
            problems.append(f"missing asset: {ASSETS_DIR / name}")
    if (ASSETS_DIR / "plaque.json").exists():
        plaque = json.loads((ASSETS_DIR / "plaque.json").read_text())
        for key in ("image", "font"):
            if not (ASSETS_DIR / plaque[key]).exists():
                problems.append(f"missing plaque {key}: {ASSETS_DIR / plaque[key]}")
    corner = load_branding().get("corner_logo")
    if corner and not (ASSETS_DIR / corner["file"]).exists():
        problems.append(f"missing corner logo: {ASSETS_DIR / corner['file']}")
    if (ASSETS_DIR / "templates.json").exists():
        for tid, t in load_templates().items():
            for clip in CLIPS:
                if not template_clip(t, clip).exists():
                    problems.append(f"template '{tid}' missing {template_clip(t, clip)}")
            for logo in load_logo_positions(tid):
                if not (ASSETS_DIR / "logos" / logo["file"]).exists():
                    problems.append(f"template '{tid}' logo missing: logos/{logo['file']}")
    return problems
