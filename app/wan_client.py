"""WaveSpeed Wan 2.2 Animate client (replace mode): upload media, submit, poll, download.

API (docs: wavespeed.ai/docs):
  POST {base}/media/uploads                    -> data.upload {url, headers}, data.download_url (kept 7 days)
  POST {base}/wavespeed-ai/wan-2.2/animate     -> data.id
  GET  {base}/predictions/{id}/result          -> data.status, data.outputs[], data.error
Statuses: created | processing (running), completed (done), failed | cancelled | timeout | deleted (terminal failure).
"""
import asyncio
import logging
import mimetypes
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("wan")

RUNNING = {"created", "processing", "pending", "queued", "starting"}
FAILED = {"failed", "cancelled", "canceled", "timeout", "deleted", "error"}


class WanError(Exception):
    pass


@dataclass
class TaskResult:
    status: str  # running | completed | failed
    outputs: list[str] = field(default_factory=list)
    error: str = ""


def _data(resp: httpx.Response) -> dict:
    body = resp.json()
    return body.get("data", body) if isinstance(body, dict) else {}


def _check(resp: httpx.Response, what: str) -> None:
    if resp.status_code >= 400:
        raise WanError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")


class WanClient:
    def __init__(self) -> None:
        self.base = config.WAVESPEED_API_BASE
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"Authorization": f"Bearer {config.WAVESPEED_API_KEY}"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def upload(self, path: Path) -> str:
        """Upload a local file, return a URL the model can read."""
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        resp = await self.http.post(
            f"{self.base}/media/uploads",
            json={"filename": path.name, "size": path.stat().st_size, "content_type": content_type},
        )
        _check(resp, "upload init")
        data = _data(resp)
        upload = data["upload"]
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as raw:  # no auth header on signed URL
            put = await raw.request(upload.get("method", "PUT"), upload["url"],
                                    headers=upload.get("headers") or {}, content=path.read_bytes())
        _check(put, "upload PUT")
        log.info("uploaded %s -> %s", path, data["download_url"])
        return data["download_url"]

    async def submit(self, image_url: str, video_url: str) -> str:
        body = {"image": image_url, "video": video_url, "mode": "replace",
                "resolution": config.WAN_RESOLUTION, "seed": -1}
        if config.WAN_PROMPT:
            body["prompt"] = config.WAN_PROMPT
        return await self.submit_model("wavespeed-ai/wan-2.2/animate", body)

    async def submit_model(self, model: str, body: dict) -> str:
        """Submit any WaveSpeed model (e.g. 'kwaivgi/kling-v4.0/image-to-video'). Never retried: a retry can bill twice."""
        resp = await self.http.post(f"{self.base}/{model}", json=body)
        _check(resp, f"submit {model}")
        task_id = _data(resp).get("id")
        if not task_id:
            raise WanError(f"submit: no task id in response {resp.text[:300]}")
        return task_id

    async def poll(self, task_id: str) -> TaskResult:
        """One status check. Raises httpx errors / WanError on transport problems (caller decides to retry)."""
        resp = await self.http.get(f"{self.base}/predictions/{task_id}/result")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise WanError(f"poll: transient HTTP {resp.status_code}")
        _check(resp, "poll")
        data = _data(resp)
        status = str(data.get("status", "")).lower()
        if status == "completed":
            outputs = [o for o in data.get("outputs") or [] if isinstance(o, str)]
            if not outputs:
                return TaskResult("failed", error="completed with no outputs")
            return TaskResult("completed", outputs)
        if status in FAILED:
            return TaskResult("failed", error=data.get("error") or status)
        return TaskResult("running")

    async def download(self, url: str, dest: Path, tries: int = 6, stall_s: float = 30.0) -> None:
        """Fetch a finished output. The CDN sometimes drops the connection mid-file, or leaves it open and stops
        sending (seen: 14 min with no bytes and httpx's read timeout never fired). The video is already paid for,
        so retry the download, resuming from the bytes we have, instead of letting the caller regenerate it.
        The stall deadline is ours (no new bytes for stall_s -> abort this attempt), not the HTTP library's."""
        tmp = dest.with_suffix(".part")
        tmp.unlink(missing_ok=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0), follow_redirects=True) as raw:
            for attempt in range(1, tries + 1):
                have = tmp.stat().st_size if tmp.exists() else 0
                try:
                    headers = {"Range": f"bytes={have}-"} if have else {}
                    async with raw.stream("GET", url, headers=headers) as resp:
                        _check(resp, "download")
                        if have and resp.status_code != 206:      # server ignored the range: start over
                            have = 0
                        chunks = resp.aiter_raw()
                        with tmp.open("ab" if have else "wb") as fh:
                            while True:
                                try:
                                    chunk = await asyncio.wait_for(anext(chunks), stall_s)
                                except StopAsyncIteration:
                                    break
                                fh.write(chunk)
                    tmp.replace(dest)
                    return
                except (httpx.TransportError, httpx.RemoteProtocolError, TimeoutError) as e:
                    if attempt == tries:
                        raise
                    log.warning("download %s: %r after %s bytes, retry %s/%s", dest.name, e,
                                tmp.stat().st_size if tmp.exists() else 0, attempt, tries - 1)
                    await asyncio.sleep(min(30, 2 ** attempt))


class MockWanClient:
    """MOCK_WAN=true: no network, no cost. The 'swapped' output is the raw template clip.

    Task ids encode everything needed to answer a poll, so mock tasks survive an app restart
    exactly like real ones: mock|<submitted_at>|<template clip path>.
    MOCK_WAN_FAIL_ONCE=B makes the first attempt of clip B fail, to exercise the auto-retry.
    """

    def __init__(self) -> None:
        self.failed_once: set[str] = set()

    async def close(self) -> None:
        pass

    async def upload(self, path: Path) -> str:
        return f"file://{path}"

    async def submit(self, image_url: str, video_url: str) -> str:
        return f"mock|{time.time():.3f}|{uuid.uuid4().hex[:6]}|{video_url.removeprefix('file://')}"

    async def poll(self, task_id: str) -> TaskResult:
        _, submitted, _, path = task_id.split("|", 3)
        if time.time() - float(submitted) < config.MOCK_WAN_DELAY_S:
            return TaskResult("running")
        clip = Path(path).stem
        if clip in config.MOCK_WAN_FAIL_ONCE and path not in self.failed_once:
            self.failed_once.add(path)
            return TaskResult("failed", error="mock failure (MOCK_WAN_FAIL_ONCE)")
        return TaskResult("completed", [f"file://{path}"])

    async def download(self, url: str, dest: Path) -> None:
        shutil.copyfile(url.removeprefix("file://"), dest)


def make_client() -> WanClient | MockWanClient:
    return MockWanClient() if config.MOCK_WAN else WanClient()
