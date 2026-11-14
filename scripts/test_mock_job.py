"""End-to-end test in MOCK_WAN mode (no API calls, no cost).

Starts the real server on a free port with a throwaway DATA_DIR, then drives it over HTTP:
  auth, photo validation, a full job (with clip B forced to fail once -> auto-retry),
  QC rerun, approve, public page + media checks (~25 s, 720x1280, audio), a hard-kill restart
  mid-job (must resume by re-polling, not resubmitting), and reject (public link must die).

Usage: .venv/bin/python scripts/test_mock_job.py [--photo path/to/real_photo.jpg] [--keep]
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import synthetic_photo  # noqa: E402

PIN = "4321"
TEMPLATES = {t["id"]: t for t in json.loads((ROOT / "assets/templates.json").read_text())}
# Wan jobs per visitor: one per clip, except a "continuous" group (e.g. B+C) is one job.
JOBS_MEN = 3 - max(0, len(TEMPLATES["men"].get("continuous") or []) - 1)
# The mock fails the first attempt of a Wan job by its input's name, which is the group's first clip. Fail the
# continuous group's job; a clip outside it (if any) must go through on its first attempt.
_CONT = TEMPLATES["men"].get("continuous") or ["B"]
FAIL_CLIP = _CONT[0]
OTHER_CLIP = next((c for c in "ABC" if c not in _CONT), None)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, data_dir: Path, port: int):
        self.port, self.proc = port, None
        self.env = {**os.environ, "MOCK_WAN": "true", "MOCK_WAN_DELAY_S": "3", "MOCK_WAN_FAIL_ONCE": FAIL_CLIP,
                    "OPERATOR_PIN": PIN, "DATA_DIR": str(data_dir), "BASE_URL": f"http://127.0.0.1:{port}",
                    "WAVESPEED_API_KEY": ""}
        self.log = open(data_dir / "server.log", "a")

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ROOT, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/login", timeout=1)
                return
            except httpx.TransportError:
                if self.proc.poll() is not None:
                    raise SystemExit(f"server exited; see {self.log.name}")
                time.sleep(0.2)
        raise SystemExit("server did not start")

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait()


def check(cond: bool, msg: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def wait_status(c: httpx.Client, job_id: int, want: str, timeout: float = 300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/jobs/{job_id}").json()
        if job["status"] == want:
            return job
        if job["status"] in ("failed", "rejected") and want not in ("failed", "rejected"):
            raise SystemExit(f"job {job_id} ended {job['status']}: {job['error']}")
        time.sleep(0.5)
    raise SystemExit(f"timeout waiting for job {job_id} -> {want}")


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", help="real photo with a visible face (default: synthetic drawing)")
    ap.add_argument("--keep", action="store_true", help="keep the temp data dir")
    args = ap.parse_args()

    subprocess.run([sys.executable, str(ROOT / "scripts/make_placeholder_assets.py")], check=True)
    data_dir = Path(tempfile.mkdtemp(prefix="lwm-test-"))
    photo = Path(args.photo) if args.photo else data_dir / "visitor.jpg"
    if not args.photo:
        synthetic_photo.make(str(photo))
    server = Server(data_dir, free_port())
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    c = httpx.Client(base_url=base, timeout=30)

    def new_job(name: str, photo_bytes: bytes | None = None, **overrides):
        fields = {"name": name, "phone": "+234 801 234 5678", "consent": "true", "template_id": "men", **overrides}
        return c.post("/jobs", data=fields, files={"photo": ("p.jpg", photo_bytes or photo.read_bytes(), "image/jpeg")})

    try:
        print("auth")
        check(c.post("/jobs").status_code == 401, "POST /jobs without session -> 401")
        check(c.get("/capture").status_code == 303, "GET /capture without session -> redirect to login")
        c.post("/login", data={"pin": "0000"})
        check(c.get("/qc").status_code == 303, "wrong PIN does not log in")
        c.post("/login", data={"pin": PIN})
        check(c.get("/capture").status_code == 200, "correct PIN -> capture page")

        print("validation")
        r = new_job("Test", photo_bytes=subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "cellauto=s=720x1280", "-frames:v", "1",
             "-f", "mjpeg", "-"], capture_output=True, check=True).stdout)
        check(r.status_code == 400 and "no face" in r.json()["detail"].lower(), f"faceless photo rejected: {r.json()}")
        r = new_job("Test", consent="")
        check(r.status_code == 400 and "consent" in r.json()["detail"], "missing consent rejected")
        r = new_job("Test", template_id="nope")
        check(r.status_code == 400, "unknown template rejected")

        print("close-up photo is refused (the film needs the whole body)")
        S_sel = data_dir / "selfie.jpg"
        from PIL import Image
        Image.open(photo).crop((180, 80, 540, 520)).resize((900, 1100)).save(S_sel)
        r = new_job("Selfie", photo_bytes=S_sel.read_bytes())
        check(r.status_code == 400 and "close-up" in r.json()["detail"], f"close-up rejected: {r.json()}")

        print(f"full job (clip {FAIL_CLIP}'s Wan job fails once, then auto-retries)")
        t0 = time.time()
        r = new_job("Ada Oluwaseun")
        check(r.status_code == 200, f"job created: {r.json()}")
        job_id = r.json()["job_id"]
        job = wait_status(c, job_id, "ready_for_qc")
        check(job["clips"][FAIL_CLIP]["attempts"] == 2   # attempts live on the group's leader clip
              and (OTHER_CLIP is None or job["clips"][OTHER_CLIP]["attempts"] == 1),
              f"{''.join(_CONT)} retried once as one job, others once ({time.time() - t0:.1f}s)")
        check(c.get(job["video_url"]).status_code == 200, "operator can preview the video")

        print("rerun clip A")
        check(c.post(f"/jobs/{job_id}/rerun/A").status_code == 200, "rerun accepted")
        check(c.post(f"/jobs/{job_id}/rerun/B").status_code == 409, "second rerun while processing -> 409")
        job = wait_status(c, job_id, "ready_for_qc")
        check(job["clips"]["A"]["attempts"] == 1, "A re-sent and job re-stitched")

        print("approve + public page")
        r = c.post(f"/jobs/{job_id}/approve").json()
        token = r["result_url"].rsplit("/", 1)[1]
        check(r["result_url"].startswith(base + "/v/") and len(token) >= 32, f"result_url {r['result_url']}")
        pub = httpx.Client(base_url=base, timeout=30)  # no cookie: a visitor's phone
        page = pub.get(f"/v/{token}")
        check(page.status_code == 200 and "Ada Oluwaseun" in page.text, "result page renders for visitor")
        poster = pub.get(f"/poster/{token}.jpg")
        check(poster.status_code == 200 and poster.content[:2] == b"\xff\xd8", "poster JPEG served")
        qr = pub.get(r["qr_png_url"])
        check(qr.status_code == 200 and qr.content[:4] == b"\x89PNG", "QR PNG served")
        check(pub.get(f"/media/{token}.mp4", headers={"Range": "bytes=0-99"}).status_code == 206,
              "media supports Range (needed for iPhone playback)")
        dl = pub.get(f"/media/{token}.mp4?download=1")
        check("attachment" in dl.headers.get("content-disposition", ""), "download sends attachment header")
        out = data_dir / "downloaded.mp4"
        out.write_bytes(dl.content)
        info = ffprobe(out)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        duration = float(info["format"]["duration"])
        check(out.stat().st_size > 100_000, f"final MP4 exists ({out.stat().st_size // 1024} KB)")
        sys.path.insert(0, str(ROOT))
        from app import ffmpeg_builder as fb
        expect = fb.timeline(TEMPLATES["men"]).total
        check(abs(duration - expect) <= 0.5, f"duration {duration:.2f}s ~ {expect}s (template timeline)")
        check((video["width"], video["height"]) == (720, 1280), f"resolution {video['width']}x{video['height']}")
        check(any(s["codec_type"] == "audio" for s in info["streams"]), "has audio stream")
        check(pub.get("/v/" + "x" * 32).status_code == 404, "unknown token -> 404")
        check(pub.get("/jobs").status_code == 401 and pub.get("/qc").status_code == 303, "visitor can't reach operator routes")

        print("restart mid-job (hard kill) resumes without resubmitting")
        r = new_job("Restart Test")
        job2 = r.json()["job_id"]
        time.sleep(1.0)  # clips submitted, mock tasks still 'running'
        check(c.get(f"/jobs/{job2}").json()["clips"]["A"]["status"] == "running", "clips running before kill")
        server.kill()
        server.env["MOCK_WAN_FAIL_ONCE"] = ""  # failure injection is per-process; keep the count deterministic
        server.start()
        job = wait_status(c, job2, "ready_for_qc")
        submits = [e for e in job["events"] if e["kind"] == "wan_submit"]
        check(any(e["kind"] == "resume" for e in job["events"]), "job resumed after restart")
        check(len(submits) == JOBS_MEN, f"exactly {JOBS_MEN} submits (re-polled, not resubmitted): {len(submits)}")

        print("reject takes the public link down")
        c.post(f"/jobs/{job_id}/reject")
        check(all(pub.get(u).status_code == 404 for u in (f"/v/{token}", f"/media/{token}.mp4", f"/poster/{token}.jpg")),
              "rejected job's page and media -> 404")
        stats = c.get("/admin/stats").json()
        expected = JOBS_MEN + 1 + 1 + JOBS_MEN   # job 1 (+1 auto-retry, +1 rerun of A's group), job 2
        check(stats["clips_sent"] == expected, f"admin counts Wan jobs sent: {stats['clips_sent']} (expected {expected})")

        print(f"\nALL CHECKS PASSED. Final video: {out}" if args.keep else "\nALL CHECKS PASSED.")
    finally:
        server.kill()
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)
        else:
            print(f"data kept in {data_dir} (server log: {data_dir / 'server.log'})")


if __name__ == "__main__":
    main()
