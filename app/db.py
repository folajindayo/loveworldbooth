"""SQLite storage. One connection, used only from the event-loop thread (queries are sub-millisecond)."""
import secrets
import sqlite3
from datetime import datetime, timezone

from . import config

ACTIVE_JOB_STATES = ("queued", "processing", "stitching")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    ticket TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    consent_at TEXT NOT NULL,
    template_id TEXT NOT NULL,
    photo_path TEXT NOT NULL,
    status TEXT NOT NULL,
    video_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    clip TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_task_id TEXT,
    output_url TEXT,
    local_path TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (job_id, clip)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    kind TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
CREATE TABLE IF NOT EXISTS media_cache (
    path TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    url TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
"""

JOB_COLUMNS = {"status", "video_path", "error", "photo_path"}
CLIP_COLUMNS = {"status", "provider_task_id", "output_url", "local_path", "attempts", "error"}

conn: sqlite3.Connection


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init() -> None:
    global conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)


def create_job(name: str, phone: str, template_id: str, photo_path: str) -> dict:
    ts = now()
    token = secrets.token_urlsafe(24)
    with conn:
        cur = conn.execute(
            "INSERT INTO jobs (token, ticket, name, phone, consent_at, template_id, photo_path, status, created_at, updated_at)"
            " VALUES (?, '', ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (token, name, phone, ts, template_id, photo_path, ts, ts),
        )
        job_id = cur.lastrowid
        ticket = f"{job_id:04d}"
        conn.execute("UPDATE jobs SET ticket = ? WHERE id = ?", (ticket, job_id))
        for clip in config.CLIPS:
            conn.execute(
                "INSERT INTO clips (job_id, clip, status, updated_at) VALUES (?, ?, 'pending', ?)",
                (job_id, clip, ts),
            )
    return get_job(job_id)


def get_job(job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_job_by_token(token: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE token = ?", (token,)).fetchone()
    return dict(row) if row else None


def list_jobs(limit: int = 200) -> list[dict]:
    rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def update_job(job_id: int, **fields) -> None:
    assert set(fields) <= JOB_COLUMNS, fields
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), now(), job_id))


def get_clips(job_id: int) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM clips WHERE job_id = ? ORDER BY clip", (job_id,)).fetchall()
    return {r["clip"]: dict(r) for r in rows}


def update_clip(job_id: int, clip: str, **fields) -> None:
    assert set(fields) <= CLIP_COLUMNS, fields
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE clips SET {sets}, updated_at = ? WHERE job_id = ? AND clip = ?",
        (*fields.values(), now(), job_id, clip),
    )
    conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (now(), job_id))


def add_event(job_id: int | None, kind: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events (job_id, kind, detail, created_at) VALUES (?, ?, ?, ?)",
        (job_id, kind, detail[:2000], now()),
    )


def job_events(job_id: int, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT kind, detail, created_at FROM events WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def unfinished_job_ids() -> list[int]:
    marks = ",".join("?" * len(ACTIVE_JOB_STATES))
    rows = conn.execute(f"SELECT id FROM jobs WHERE status IN ({marks}) ORDER BY id", ACTIVE_JOB_STATES).fetchall()
    return [r["id"] for r in rows]


def get_cached_upload(path: str, fingerprint: str, max_age_s: int) -> str | None:
    row = conn.execute("SELECT * FROM media_cache WHERE path = ?", (path,)).fetchone()
    if not row or row["fingerprint"] != fingerprint:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["uploaded_at"])
    return row["url"] if age.total_seconds() < max_age_s else None


def put_cached_upload(path: str, fingerprint: str, url: str) -> None:
    conn.execute(
        "INSERT INTO media_cache (path, fingerprint, url, uploaded_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(path) DO UPDATE SET fingerprint = excluded.fingerprint, url = excluded.url,"
        " uploaded_at = excluded.uploaded_at",
        (path, fingerprint, url, now()),
    )


def stats() -> dict:
    by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    by_event = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, COUNT(*) n FROM events GROUP BY kind")}
    durations = conn.execute(
        "SELECT (julianday(e.created_at) - julianday(j.created_at)) * 1440 AS minutes"
        " FROM events e JOIN jobs j ON j.id = e.job_id"
        " WHERE e.kind = 'ready_for_qc' AND e.id = (SELECT MIN(id) FROM events WHERE job_id = j.id AND kind = 'ready_for_qc')"
    ).fetchall()
    mins = sorted(r["minutes"] for r in durations)
    return {
        "jobs_by_status": by_status,
        "clips_sent": by_event.get("wan_submit", 0),
        "clip_failures": by_event.get("wan_fail", 0),
        "stitch_failures": by_event.get("stitch_fail", 0),
        "reruns": by_event.get("rerun", 0),
        "first_run_approvals": conn.execute(
            "SELECT COUNT(*) FROM jobs j WHERE status = 'approved'"
            " AND NOT EXISTS (SELECT 1 FROM events e WHERE e.job_id = j.id AND e.kind = 'rerun')"
        ).fetchone()[0],
        "minutes_to_ready": {
            "count": len(mins),
            "median": round(mins[len(mins) // 2], 1) if mins else None,
            "max": round(mins[-1], 1) if mins else None,
        },
    }
