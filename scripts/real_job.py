"""Run ONE real job end to end against WaveSpeed (costs ~3 Wan clips). Use before the event and after new templates.

Starts the app locally with MOCK_WAN=false on a throwaway DATA_DIR, submits a visitor photo through
the normal capture route, waits for ready_for_qc and copies the final video next to the photo.

Usage: .venv/bin/python scripts/real_job.py --photo visitor.jpg --template men [--name "Test Visitor"]
"""
import argparse
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", required=True)
    ap.add_argument("--template", default="men")
    ap.add_argument("--name", default="Test Visitor")
    ap.add_argument("--out", help="where to copy the final video (default: next to the photo)")
    args = ap.parse_args()

    data_dir = Path(tempfile.mkdtemp(prefix="lwm-real-"))
    port = free_port()
    env = {**os.environ, "MOCK_WAN": "false", "OPERATOR_PIN": "real-job", "DATA_DIR": str(data_dir),
           "BASE_URL": f"http://127.0.0.1:{port}"}
    log = open(data_dir / "server.log", "w")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60)
        for _ in range(100):
            try:
                c.get("/login")
                break
            except httpx.TransportError:
                if server.poll() is not None:
                    sys.exit(f"server exited; see {data_dir / 'server.log'}")
                time.sleep(0.3)
        c.post("/login", data={"pin": "real-job"})
        r = c.post("/jobs", data={"name": args.name, "phone": "+2340000000000", "consent": "true",
                                  "template_id": args.template},
                   files={"photo": (Path(args.photo).name, Path(args.photo).read_bytes(), "image/jpeg")})
        if r.status_code != 200:
            sys.exit(f"photo rejected: {r.json()}")
        job_id = r.json()["job_id"]
        print(f"job {job_id} submitted; logs: {data_dir / 'server.log'}")
        t0, last = time.time(), None
        while True:
            job = c.get(f"/jobs/{job_id}").json()
            state = (job["status"], tuple((k, v["status"], v["attempts"]) for k, v in job["clips"].items()))
            if state != last:
                clips = " ".join(f"{k}:{s}/{a}" for k, s, a in state[1])
                print(f"  {time.time() - t0:5.0f}s  {job['status']:12s} {clips}")
                last = state
            if job["status"] in ("ready_for_qc", "failed", "rejected"):
                break
            time.sleep(5)
        if job["status"] != "ready_for_qc":
            sys.exit(f"job ended {job['status']}: {job['error']}")
        out = Path(args.out) if args.out else Path(args.photo).with_name(f"real_job_{args.template}.mp4")
        shutil.copyfile(data_dir / "jobs" / str(job_id) / "final.mp4", out)
        for clip in "ABC":
            shutil.copyfile(data_dir / "jobs" / str(job_id) / f"{clip}.mp4", out.with_name(f"{out.stem}_{clip}.mp4"))
        print(f"done in {time.time() - t0:.0f}s -> {out} (+ swapped clips _A/_B/_C)")
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
