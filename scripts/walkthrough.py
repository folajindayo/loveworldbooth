"""Visual walkthrough of the whole booth flow, captured as phone screenshots.

Starts the app locally (real WaveSpeed unless --mock) on a throwaway DATA_DIR, then drives headless Chrome
like an operator and a visitor would: landing -> login -> capture form -> ticket/status -> QC (processing,
then ready with video) -> approve + QR -> the visitor's page. Screenshots go to --out (default raw/walkthrough/).

Usage: .venv/bin/python scripts/walkthrough.py --photo visitor.jpg --name "Ada Okafor" --template women [--mock]
Dev tool only: needs Google Chrome and the websocket-client package (pip install websocket-client).
"""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PIN = "walkthrough"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tab:
    """Minimal Chrome DevTools Protocol client: navigate, evaluate JS, screenshot."""

    def __init__(self, port: int):
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.n = 0
        self.call("Page.enable")
        self.call("Emulation.setDeviceMetricsOverride",
                  width=390, height=844, deviceScaleFactor=2, mobile=True)
        self.call("Emulation.setUserAgentOverride",
                  userAgent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def go(self, url: str, settle: float = 2.5):
        self.call("Page.navigate", url=url)
        time.sleep(settle)

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    def shot(self, path: Path, full: bool = False):
        params = {"format": "png"}
        if full:
            h = self.js("Math.max(document.body.scrollHeight, 844)")
            params["clip"] = {"x": 0, "y": 0, "width": 390, "height": h, "scale": 1}
            params["captureBeyondViewport"] = True
        data = self.call("Page.captureScreenshot", **params)["data"]
        path.write_bytes(base64.b64decode(data))
        print("  screenshot", path.name)

    def set_file(self, selector: str, file: Path):
        doc = self.call("DOM.getDocument")
        node = self.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=selector)["nodeId"]
        self.call("DOM.setFileInputFiles", nodeId=node, files=[str(file)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--template", default="women")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "raw/walkthrough"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    data_dir = Path(tempfile.mkdtemp(prefix="lwm-walk-"))
    port, cport = free_port(), free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "MOCK_WAN": "true" if args.mock else "false", "OPERATOR_PIN": PIN,
           "DATA_DIR": str(data_dir), "BASE_URL": base, "MOCK_WAN_DELAY_S": "5"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=open(data_dir / "server.log", "w"), stderr=subprocess.STDOUT)
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={cport}",
                               f"--user-data-dir={data_dir / 'chrome'}", "--use-angle=swiftshader",
                               "--enable-unsafe-swiftshader", "--hide-scrollbars", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)
        tab = Tab(cport)
        t0 = time.time()

        print("1. visitor-facing landing page")
        tab.go(base + "/", settle=4)
        tab.shot(out / "01_landing.png")

        print("2. operator logs in")
        tab.go(base + "/capture")
        tab.js(f"document.getElementById('pin').value = {json.dumps(PIN)}")
        tab.shot(out / "02_login.png")
        tab.js("document.querySelector('form').submit()")
        time.sleep(2)

        print("3. capture form, filled in")
        tab.js(f"document.getElementById('name').value = {json.dumps(args.name)}")
        tab.js("document.getElementById('phone').value = '+234 801 234 5678'")
        tab.js(f"document.getElementById('tpl-{args.template}').checked = true")
        tab.set_file("#photo", Path(args.photo).resolve())
        tab.js("document.getElementById('photo').dispatchEvent(new Event('change'))")
        tab.js("document.getElementById('consent').checked = true")
        time.sleep(1)
        tab.shot(out / "03_capture_filled.png", full=True)

        print("4. submit -> ticket + live status")
        tab.js("document.getElementById('submit').click()")
        time.sleep(6)
        tab.shot(out / "04_ticket_status.png")

        print("5. QC while processing")
        tab.go(base + "/qc", settle=3)
        tab.shot(out / "05_qc_processing.png", full=True)

        print("6. waiting for the film (real Wan: ~4 min)")
        job = None
        while time.time() - t0 < 900:
            job = tab.js("fetch('/jobs/1').then(r => r.json())")
            if job and job["status"] in ("ready_for_qc", "failed"):
                break
            time.sleep(5)
        print(f"   job status {job['status']} after {time.time() - t0:.0f}s")
        if job["status"] != "ready_for_qc":
            sys.exit(f"job failed: {job.get('error')}")

        print("7. QC: ready for review")
        tab.go(base + "/qc", settle=4)
        tab.shot(out / "07_qc_ready.png", full=True)

        print("8. approve -> QR code")
        tab.js("[...document.querySelectorAll('.actions button')].find(b => b.textContent === 'Approve').click()")
        time.sleep(3)
        tab.shot(out / "08_qr.png")
        result_url = tab.js("document.getElementById('modal-url').textContent")

        print("9. the visitor's page (as opened from the QR code)")
        tab.go(result_url, settle=5)
        tab.shot(out / "09_result.png")
        tab.js("scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
        tab.shot(out / "10_result_buttons.png")

        shutil.copyfile(data_dir / "jobs/1/final.mp4", out / "film.mp4")
        print(f"done in {time.time() - t0:.0f}s -> {out}")
    finally:
        chrome.terminate()
        server.terminate()
        chrome.wait()
        server.wait()


if __name__ == "__main__":
    main()
