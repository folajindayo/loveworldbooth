# LoveWorld Moment

An operator photographs a visitor. The app swaps the visitor into three pre-made award scenes
using Wan 2.2 Animate (replace mode) on WaveSpeed, then stitches a 25 s vertical video with
ffmpeg. After QC approval, the visitor scans a QR code that opens their video page.

## How it works

```
capture page ──POST /jobs──▶ photo check (OpenCV) ──▶ jobs + clips rows (SQLite)
                                                        │
                       worker: one asyncio task per job ▼
     ┌── clip A ──┐   upload photo + template clip (cached URLs)
     ├── clip B ──┤── submit Wan task ─▶ poll every 5 s ─▶ download ─▶ ffprobe
     └── clip C ──┘   (≤ MAX_CONCURRENT_WAN_JOBS at once, 1 automatic retry per clip)
                                                        │
                ffmpeg stitch (≤ MAX_CONCURRENT_FFMPEG) ▼
                                     status ready_for_qc ──▶ QC: approve / rerun clip / reject
                                                        │
                       approved ──▶ /v/{token} page, /media/{token}.mp4, /qr/{token}.png
```

- **State lives in SQLite, not memory.** Each step re-reads the DB. After a restart, any job
  that isn't finished resumes. A Wan task that was running is re-polled by its
  `provider_task_id` and never resubmitted, so nothing is paid for twice.
- **One process.** The worker, semaphores and DB connection all live inside the web process.
  There's no Redis or Celery. Never run uvicorn with `--workers`.
- **Every API call and ffmpeg command is logged with `job=<id>`** (stdout / journald). Each job
  also keeps an event trail (`GET /jobs/{id}` → `events`): submits, failures, retries, reruns,
  stitch time. To debug a render, copy the logged ffmpeg command and run it by hand.

| File | Job |
|---|---|
| `app/main.py` | routes, PIN session, pages, public video routes |
| `app/worker.py` | pipeline: per-clip state machine, retries, resume, stitching |
| `app/wan_client.py` | WaveSpeed client (upload, submit, poll, download) + `MockWanClient` |
| `app/ffmpeg_builder.py` | builds the single ffmpeg command, renders the watermark |
| `app/plaque.py` | engraves the visitor's name on the plaque, composes the 9:16 plaque frame |
| `app/photo_check.py` | face / blur / size checks, 9:16 crop centred on the face |
| `app/db.py` | schema + queries |
| `app/config.py` | `.env` settings, template/asset loading, startup validation |

### Final video (720x1280, 30 fps, H.264 + AAC, 25.0 s)

| Time | Segment | Source |
|---|---|---|
| 0–3 s | Intro | `intro.mp4` |
| 3–8 s | Showcase | swapped clip A + logos at `logo_positions.json` |
| 8–13 s | Runway entrance | swapped clip B |
| 13–18 s | Award presentation | swapped clip C |
| 18–22 s | Plaque | the award plaque with the visitor's name engraved on the nameplate, eased push-in from the whole plaque to the name |
| 22–25 s | End card | `endcard.mp4` |

The whole video also gets one colour grade (`eq`), light grain, a corner watermark ("A LoveWorld
Expo Moment"), and music trimmed to 25 s with a 1 s fade-out. Every input is normalised to
720x1280 / 30 fps / yuv420p before concat. A clip that's too short holds its last frame, and
one that's too long is cut.

Two deliberate deviations from the brief:

- **The name is engraved on the award plaque instead of shown as on-screen text.**
  `app/plaque.py` renders it into the plaque's nameplate (`assets/plaque.json` sets the box, font
  and colour), with long names wrapping onto two lines. The watermark is also drawn with Pillow
  rather than `drawtext`. Visitor names never pass through ffmpeg
  filter escaping, long names shrink to fit, and any ffmpeg build works. Some builds, such as
  Homebrew's, ship without freetype and so without `drawtext`.
- **Grain is lighter and the bitrate is capped.** Grain is `noise=alls=4` and there's a
  `-maxrate 4M` cap. Per-frame grain doesn't compress: at `alls=6` the 25 s file was 41 MB.
  Now it's about 4 MB on test footage and at most about 12.5 MB, which matters for visitors on
  mobile data.

## Local setup

Requirements: Python 3.11+ and ffmpeg/ffprobe on PATH.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                       # set OPERATOR_PIN; MOCK_WAN=true for a free run
.venv/bin/python scripts/make_placeholder_assets.py
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/capture, log in with the PIN, and submit a visitor. QC is at
`/qc` and stats are at `/admin`. If `.env` or the assets are incomplete, the app refuses to
start and lists exactly what's missing.

## Test run (MOCK_WAN, free)

```bash
.venv/bin/python scripts/test_mock_job.py            # add --keep to inspect the output video
.venv/bin/python scripts/test_mock_job.py --photo me.jpg   # use a real photo instead of the synthetic one
```

The test starts the real server on a free port with a throwaway data dir and drives it over HTTP.
It checks:

- auth: unauthenticated requests are refused, and a wrong PIN doesn't log in
- photo validation: no face, missing consent, unknown template
- a full job in which clip B fails its first attempt and auto-retries
- rerunning clip A from QC, plus a second rerun refused while the job is processing
- approve, then as a visitor: the result page, QR PNG, Range requests and the download header
- **the final MP4 is about 25 s, 720x1280, and has an audio stream**
- a hard kill (SIGKILL) mid-job, then restart: the job resumes with exactly 3 submits
- reject, after which the public page and media return 404, plus the admin counters

A full mock job takes about 10 s.

## WaveSpeed notes (read before the event)

- **Concurrency is set by your account tier.** New accounts (Bronze) allow 2 concurrent tasks
  and 5 submits per minute. Silver allows 300 concurrent. `MAX_CONCURRENT_WAN_JOBS` must not
  exceed the limit.
- **Throughput math.** Each visitor needs 3 tasks of about 4 min each. With N slots,
  capacity is about `N / 3 / 4` visitors per minute, and a visitor's wait is about 4 min plus
  their queue time.
  - With the default N=6: about 0.5 visitors/min (30 per hour), and the ≤ 10 min target holds
    only if fewer than about 2 visitors arrive every 4 minutes.
  - **20 jobs in progress within 10 min needs N ≈ 60** (Silver tier). The web server never
    blocks either way; only the wait grows.
- **Cost.** 720p costs $0.08/s, so one 5 s clip is $0.40 and one visitor is $1.20, plus retries
  and reruns. 480p (`WAN_RESOLUTION=480p`) costs half. Check the live price on WaveSpeed. The
  admin page counts clips sent, retries included.
- **Uploads.** Template clips are uploaded once and their URLs reused for 6 days, since
  WaveSpeed keeps uploads for 7.
- **Ambiguous submits.** If a submit request times out, WaveSpeed may still have created a
  task. The app counts it as a failed attempt and uses the one automatic retry, so the worst
  case is one extra billed clip. It never retries a submit in a loop.
- `wan_client.py` follows the documented API: `POST /api/v3/media/uploads` → PUT, then
  `POST /api/v3/wavespeed-ai/wan-2.2/animate` with `mode: "replace"`, then
  `GET /api/v3/predictions/{id}/result`. Before the event, run one real job and watch the logs.

## Operating it

- **Capture** (`/capture`, phone): name, phone, template, photo, consent. A bad photo is
  rejected immediately with a retake instruction. The blur message shows the measured
  sharpness, so `BLUR_THRESHOLD` can be tuned on the day. On success it shows a ticket number
  and live status.
- **QC** (`/qc`, refreshes every 5 s): play, then **Approve** (shows a large QR code),
  **Rerun A/B/C** (re-sends one clip, then re-stitches), or **Reject**. Rejecting an approved
  video takes the public link down. Failed jobs show the provider's error per clip.
- **Admin** (`/admin`): jobs by status, clips sent, failures, reruns, first-run approval
  rate, and median/max minutes from photo to ready.
- **Public:** `/v/{token}` (192-bit random token), `/media/{token}.mp4` and `/qr/{token}.png`
  serve approved jobs only. Anything else returns 404. There's no directory listing, and data
  files are reachable only through these checked routes.
- **Consent:** each job stores `consent_at` (UTC). Photos, clips and videos live in
  `data/jobs/<id>/`, and nothing is auto-deleted (out of scope).

## VPS deploy (Ubuntu 24.04)

```bash
# 1. System packages
sudo apt update && sudo apt install -y python3-venv ffmpeg caddy git
#    (caddy: if not in your apt sources, follow https://caddyserver.com/docs/install#debian-ubuntu-raspbian)

# 2. App user + code
sudo useradd --system --home /opt/loveworld-moment --shell /usr/sbin/nologin lwm
sudo mkdir -p /opt/loveworld-moment && sudo chown lwm:lwm /opt/loveworld-moment
sudo -u lwm git clone <repo-url> /opt/loveworld-moment      # or rsync the folder
cd /opt/loveworld-moment
sudo -u lwm python3 -m venv .venv
sudo -u lwm .venv/bin/pip install -r requirements.txt

# 3. Assets + config
#    copy the real assets into /opt/loveworld-moment/assets (see assets/README.md)
sudo -u lwm cp .env.example .env && sudo -u lwm nano .env   # API key, BASE_URL, PIN, MOCK_WAN=false
sudo chmod 600 .env

# 4. Smoke test on the server (free, mock mode)
sudo -u lwm .venv/bin/python scripts/test_mock_job.py

# 5. Service
sudo cp deploy/loveworld-moment.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now loveworld-moment
sudo journalctl -u loveworld-moment -f          # should say "started (MOCK_WAN=False ...)"

# 6. HTTPS
#    point the domain's A record at the VPS, then:
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo nano /etc/caddy/Caddyfile   # set the hostname
sudo systemctl reload caddy
```

Updating: `git pull && sudo systemctl restart loveworld-moment`. Jobs in flight resume on
their own.

Backups: `data/` holds everything (the SQLite DB plus job files). Use
`sqlite3 data/app.db ".backup backup.db"` for a consistent DB copy while running.

Ubuntu 24.04's ffmpeg (6.1) works as is. The app needs only `libx264`, `aac` and standard
filters.
