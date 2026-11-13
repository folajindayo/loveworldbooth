"""Background pipeline. One asyncio task per job; semaphores cap Wan tasks and ffmpeg processes.

State lives in SQLite, not in memory: every step re-reads the DB, so a restart resumes where it
stopped (running Wan tasks are re-polled by provider_task_id, never resubmitted).
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path

from . import config, db
from . import ffmpeg_builder as fb
from .wan_client import TaskResult, make_client

log = logging.getLogger("worker")

UPLOAD_MAX_AGE_S = 6 * 24 * 3600  # WaveSpeed keeps uploads 7 days
MAX_POLL_ERRORS = 30


class Worker:
    def __init__(self) -> None:
        self.client = make_client()
        self.wan_slots = asyncio.Semaphore(config.MAX_CONCURRENT_WAN_JOBS)
        self.ffmpeg_slots = asyncio.Semaphore(config.MAX_CONCURRENT_FFMPEG)
        self.upload_lock = asyncio.Lock()
        self.tasks: dict[int, asyncio.Task] = {}

    # ---- lifecycle -------------------------------------------------------------------------
    def resume(self) -> None:
        for job_id in db.unfinished_job_ids():
            log.info("job=%s resuming after restart", job_id)
            db.add_event(job_id, "resume")
            self.start(job_id)

    def start(self, job_id: int) -> bool:
        """Schedule a job. Returns False if it's already running."""
        if self.is_running(job_id):
            return False
        task = asyncio.create_task(self._run_job(job_id), name=f"job-{job_id}")
        self.tasks[job_id] = task
        task.add_done_callback(lambda _t, j=job_id: self.tasks.pop(j, None))
        return True

    def is_running(self, job_id: int) -> bool:
        t = self.tasks.get(job_id)
        return t is not None and not t.done()

    def cancel(self, job_id: int) -> None:
        t = self.tasks.get(job_id)
        if t and not t.done():
            t.cancel()

    async def shutdown(self) -> None:
        for t in list(self.tasks.values()):
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.client.close()

    # ---- pipeline --------------------------------------------------------------------------
    async def _run_job(self, job_id: int) -> None:
        try:
            await self._pipeline(job_id)
        except asyncio.CancelledError:
            log.info("job=%s task cancelled", job_id)
            raise
        except Exception as e:  # never fail silently: the operator sees this on the QC page
            log.exception("job=%s crashed", job_id)
            db.update_job(job_id, status="failed", error=f"Internal error: {e}")
            db.add_event(job_id, "crash", repr(e))

    async def _pipeline(self, job_id: int) -> None:
        job = db.get_job(job_id)
        if not job or job["status"] not in db.ACTIVE_JOB_STATES:
            return
        db.update_job(job_id, status="processing", error=None)
        results = await asyncio.gather(*(self._run_clip(job, c) for c in config.CLIPS))
        if not all(results):
            if db.get_job(job_id)["status"] == "rejected":
                return
            clips = db.get_clips(job_id)
            failed = ", ".join(f"{c}: {clips[c]['error']}" for c in config.CLIPS if clips[c]["status"] == "failed")
            db.update_job(job_id, status="failed", error=f"Clip(s) failed after retry — {failed}")
            db.add_event(job_id, "job_failed", failed)
            return
        await self._stitch(job_id)

    async def _stitch(self, job_id: int) -> None:
        job = db.get_job(job_id)
        if job["status"] == "rejected":
            return
        db.update_job(job_id, status="stitching")
        clips = db.get_clips(job_id)
        paths = {c: Path(clips[c]["local_path"]) for c in config.CLIPS}
        async with self.ffmpeg_slots:
            t0 = time.monotonic()
            try:
                out = await fb.stitch(job, paths, job_dir(job_id))
                info = await fb.probe(out)
            except fb.FFmpegError as e:
                db.update_job(job_id, status="failed", error=f"Video assembly failed: {e}")
                db.add_event(job_id, "stitch_fail", str(e))
                return
        db.add_event(job_id, "stitched", f"{time.monotonic() - t0:.1f}s {info}")
        if db.get_job(job_id)["status"] == "rejected":
            return
        db.update_job(job_id, status="ready_for_qc", video_path=str(out), error=None)
        db.add_event(job_id, "ready_for_qc")
        log.info("job=%s ready_for_qc", job_id)

    async def _run_clip(self, job: dict, clip: str) -> bool:
        """Drive one clip to done. Returns True on success, False once retries are exhausted."""
        job_id = job["id"]
        dest = job_dir(job_id) / f"{clip}.mp4"
        while True:
            row = db.get_clips(job_id)[clip]
            if row["status"] == "done" and row["local_path"] and Path(row["local_path"]).exists():
                return True
            if db.get_job(job_id)["status"] == "rejected":
                return False
            async with self.wan_slots:
                if row["status"] == "running" and row["provider_task_id"]:
                    task_id = row["provider_task_id"]
                    log.info("job=%s clip=%s re-polling task %s", job_id, clip, task_id)
                elif row["attempts"] >= config.WAN_MAX_ATTEMPTS:
                    return False
                else:
                    task_id = await self._submit(job, clip, row["attempts"] + 1)
                if task_id:
                    result = await self._wait(job_id, clip, task_id)
                else:
                    result = TaskResult("failed", error=db.get_clips(job_id)[clip]["error"])
            if db.get_job(job_id)["status"] == "rejected":
                return False
            if result.status == "completed":
                try:
                    db.update_clip(job_id, clip, output_url=result.outputs[0])
                    raw = dest.with_name(f"{clip}.wan.mp4")
                    await self.client.download(result.outputs[0], raw)
                    await fb.probe(raw)
                    await self._finish_clip(job, clip, raw, dest)
                    await fb.probe(dest)
                    db.update_clip(job_id, clip, status="done", local_path=str(dest), error=None)
                    db.add_event(job_id, "wan_done", clip)
                    log.info("job=%s clip=%s done", job_id, clip)
                    return True
                except Exception as e:
                    result = TaskResult("failed", error=f"download/validate failed: {e}")
            attempts = db.get_clips(job_id)[clip]["attempts"]
            db.update_clip(job_id, clip, status="failed", provider_task_id=None, error=result.error)
            db.add_event(job_id, "wan_fail", f"{clip} attempt {attempts}: {result.error}")
            log.warning("job=%s clip=%s attempt %s failed: %s", job_id, clip, attempts, result.error)
            if attempts >= config.WAN_MAX_ATTEMPTS:
                return False
            db.update_clip(job_id, clip, status="pending")  # loop around for the automatic retry

    async def _submit(self, job: dict, clip: str, attempt: int) -> str | None:
        job_id = job["id"]
        template = config.load_templates()[job["template_id"]]
        try:
            image_url = await self._upload(Path(job["photo_path"]))
            video_url = await self._upload(await self._swap_input(template, clip))
            db.update_clip(job_id, clip, status="running", attempts=attempt, provider_task_id=None, error=None)
            task_id = await self.client.submit(image_url, video_url)
        except Exception as e:
            # Submits are not retried blindly here: an ambiguous failure may still have created a task.
            # It counts as an attempt; the one automatic retry then covers it.
            db.update_clip(job_id, clip, status="failed", attempts=attempt, error=f"submit failed: {e}")
            log.warning("job=%s clip=%s submit failed: %s", job_id, clip, e)
            return None
        db.update_clip(job_id, clip, provider_task_id=task_id)
        db.add_event(job_id, "wan_submit", f"{clip} attempt {attempt} task {task_id}")
        log.info("job=%s clip=%s submitted task %s (attempt %s)", job_id, clip, task_id, attempt)
        return task_id

    async def _wait(self, job_id: int, clip: str, task_id: str) -> TaskResult:
        deadline = time.monotonic() + config.WAN_TIMEOUT_S
        errors = 0
        poll_s = 1 if config.MOCK_WAN else config.WAN_POLL_S
        while time.monotonic() < deadline:
            if db.get_job(job_id)["status"] == "rejected":
                return TaskResult("failed", error="job rejected")
            try:
                result = await self.client.poll(task_id)
                errors = 0
            except Exception as e:  # transport hiccups: keep polling, give up after many in a row
                errors += 1
                log.warning("job=%s clip=%s poll error %s/%s: %s", job_id, clip, errors, MAX_POLL_ERRORS, e)
                if errors >= MAX_POLL_ERRORS:
                    return TaskResult("failed", error=f"polling kept failing: {e}")
                await asyncio.sleep(min(30, poll_s * 2))
                continue
            if result.status != "running":
                db.add_event(job_id, "wan_poll", f"{clip} task {task_id}: {result.status} {result.error}")
                return result
            await asyncio.sleep(poll_s)
        return TaskResult("failed", error=f"timed out after {config.WAN_TIMEOUT_S}s")

    async def _swap_input(self, template: dict, clip: str) -> Path:
        """The clip Wan sees: the template itself, or (for clips with a swap region) only the stand-in's region."""
        src = config.template_clip(template, clip)
        region = fb.swap_region(template, clip)
        if not region:
            return src
        key = f"{template['id']}_{region['x']}_{region['width']}_{src.stat().st_mtime_ns}"
        out = config.DATA_DIR / "swap_inputs" / key / f"{clip}.mp4"   # file keeps the clip's name
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.mp4")
            async with self.ffmpeg_slots:
                await fb.run(fb.swap_input_cmd(src, region, tmp))
            tmp.replace(out)
        return out

    async def _finish_clip(self, job: dict, clip: str, raw: Path, dest: Path) -> None:
        """Wan output -> the clip used in the stitch (blended back into the template if it has a swap region)."""
        template = config.load_templates()[job["template_id"]]
        region = fb.swap_region(template, clip)
        if not region:
            raw.replace(dest)
            return
        mask = config.DATA_DIR / "swap_inputs" / f"mask_{region['x']}_{region['width']}_{region.get('feather', 40)}.png"
        if not mask.exists():
            fb.region_mask(region, mask)
        tmp = dest.with_suffix(".tmp.mp4")
        async with self.ffmpeg_slots:
            await fb.run(fb.composite_cmd(config.template_clip(template, clip), raw, mask, tmp), job["id"])
        tmp.replace(dest)

    async def _upload(self, path: Path) -> str:
        """Upload once and reuse the URL (template clips are shared by every job)."""
        st = path.stat()
        fingerprint = hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()
        async with self.upload_lock:
            url = db.get_cached_upload(str(path), fingerprint, UPLOAD_MAX_AGE_S)
            if not url:
                url = await self.client.upload(path)
                db.put_cached_upload(str(path), fingerprint, url)
        return url


def job_dir(job_id: int) -> Path:
    d = config.JOBS_DIR / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
