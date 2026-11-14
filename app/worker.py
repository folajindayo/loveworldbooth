"""Background pipeline. One asyncio task per job; semaphores cap Wan tasks and ffmpeg processes.

State lives in SQLite, not in memory: every step re-reads the DB, so a restart resumes where it
stopped (running Wan tasks are re-polled by provider_task_id, never resubmitted).
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path

from .composite import region_bounds
from . import config, db
from . import composite
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
        groups = fb.clip_groups(config.load_templates()[job["template_id"]])
        results = await asyncio.gather(*(self._run_clip(job, g) for g in groups))
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

    def _mark(self, job_id: int, group: list[str], **fields) -> None:
        """Status/error apply to every clip in the group; attempts and task id live on the leader (group[0])."""
        for c in group:
            db.update_clip(job_id, c, **fields)

    async def _run_clip(self, job: dict, group: list[str]) -> bool:
        """Drive one Wan job (a clip, or a continuous group of clips) to done. False once retries are exhausted."""
        job_id = job["id"]
        clip = group[0]
        while True:
            rows = db.get_clips(job_id)
            if all(rows[c]["status"] == "done" and rows[c]["local_path"] and Path(rows[c]["local_path"]).exists()
                   for c in group):
                return True
            row = rows[clip]
            if db.get_job(job_id)["status"] == "rejected":
                return False
            async with self.wan_slots:
                if row["status"] == "running" and row["provider_task_id"]:
                    task_id = row["provider_task_id"]
                    log.info("job=%s clip=%s re-polling task %s", job_id, clip, task_id)
                elif row["attempts"] >= config.WAN_MAX_ATTEMPTS:
                    return False
                else:
                    task_id = await self._submit(job, group, row["attempts"] + 1)
                if task_id:
                    result = await self._wait(job_id, clip, task_id)
                else:
                    result = TaskResult("failed", error=db.get_clips(job_id)[clip]["error"])
            if db.get_job(job_id)["status"] == "rejected":
                return False
            if result.status == "completed":
                try:
                    db.update_clip(job_id, clip, output_url=result.outputs[0])
                    raw = job_dir(job_id) / f"{''.join(group)}.wan.mp4"
                    await self.client.download(result.outputs[0], raw)
                    await fb.probe(raw)
                    raw = await self._face_pass(job, group, raw)
                    for c, dest in (await self._finish_group(job, group, raw)).items():
                        await fb.probe(dest)
                        db.update_clip(job_id, c, status="done", local_path=str(dest), error=None)
                    db.add_event(job_id, "wan_done", "".join(group))
                    log.info("job=%s clip(s)=%s done", job_id, "".join(group))
                    return True
                except Exception as e:
                    result = TaskResult("failed", error=f"download/validate failed: {e}")
            attempts = db.get_clips(job_id)[clip]["attempts"]
            db.update_clip(job_id, clip, provider_task_id=None)
            self._mark(job_id, group, status="failed", error=result.error)
            db.add_event(job_id, "wan_fail", f"{''.join(group)} attempt {attempts}: {result.error}")
            log.warning("job=%s clip(s)=%s attempt %s failed: %s", job_id, "".join(group), attempts, result.error)
            if attempts >= config.WAN_MAX_ATTEMPTS:
                return False
            self._mark(job_id, group, status="pending")  # loop around for the automatic retry

    async def _submit(self, job: dict, group: list[str], attempt: int) -> str | None:
        job_id = job["id"]
        clip = group[0]
        template = config.load_templates()[job["template_id"]]
        try:
            image_url = await self._upload(Path(job["photo_path"]))
            video_url = await self._upload(await self._swap_input(template, group))
            db.update_clip(job_id, clip, attempts=attempt, provider_task_id=None)
            self._mark(job_id, group, status="running", error=None)
            if config.SWAP_ENGINE == "seedance" and not config.MOCK_WAN:
                face = job_dir(job_id) / "face.jpg"
                refs = [image_url] + ([await self._upload(face)] if face.exists() else [])
                task_id = await self.client.submit_model(config.SEEDANCE_EDIT_MODEL, {
                    "prompt": config.SEEDANCE_EDIT_PROMPT, "video": video_url, "reference_images": refs,
                    "resolution": "720p", "generate_audio": False})
            else:
                task_id = await self.client.submit(image_url, video_url)
        except Exception as e:
            # Submits are not retried blindly here: an ambiguous failure may still have created a task.
            # It counts as an attempt; the one automatic retry then covers it.
            db.update_clip(job_id, clip, attempts=attempt)
            self._mark(job_id, group, status="failed", error=f"submit failed: {e}")
            log.warning("job=%s clip(s)=%s submit failed: %s", job_id, "".join(group), e)
            return None
        db.update_clip(job_id, clip, provider_task_id=task_id)
        db.add_event(job_id, "wan_submit", f"{''.join(group)} attempt {attempt} task {task_id}")
        log.info("job=%s clip(s)=%s submitted task %s (attempt %s)", job_id, "".join(group), task_id, attempt)
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
                log.warning("job=%s clip=%s poll error %s/%s: %r", job_id, clip, errors, MAX_POLL_ERRORS, e)
                if errors >= MAX_POLL_ERRORS:
                    return TaskResult("failed", error=f"polling kept failing: {e}")
                await asyncio.sleep(min(30, poll_s * 2))
                continue
            if result.status != "running":
                db.add_event(job_id, "wan_poll", f"{clip} task {task_id}: {result.status} {result.error}")
                return result
            await asyncio.sleep(poll_s)
        return TaskResult("failed", error=f"timed out after {config.WAN_TIMEOUT_S}s")

    async def _face_pass(self, job: dict, group: list[str], raw: Path) -> Path:
        """Optional face-identity pass on Wan's output. It sees the same one-person strip Wan saw, so the
        "largest face" it replaces is always the visitor's. Any failure falls back to Wan's output: never fail
        a visitor's film over the refinement step, but log it so QC can see it."""
        face = job_dir(job["id"]) / "face.jpg"
        if not config.FACE_SWAP or config.MOCK_WAN or not face.exists():
            return raw
        # Runs after Seedance too: Seedance carries the hair, glasses and outfit, but its face is a likeness
        # (generic grin, lost moustache on the test photo); this pass sets the exact face from the photo.
        label = "".join(group)
        try:
            async with self.wan_slots:
                body = {"video": await self.client.upload(raw), "face_image": await self.client.upload(face),
                        "target_index": 0, "target_gender": "all"}
                task_id = await self.client.submit_model(config.FACE_SWAP_MODEL, body)
                db.add_event(job["id"], "face_submit", f"{label} task {task_id}")
                result = await self._wait(job["id"], group[0], task_id)
            if result.status != "completed":
                raise RuntimeError(result.error or result.status)
            out = raw.with_name(f"{label}.face.mp4")
            await self.client.download(result.outputs[0], out)
            await fb.probe(out)
            db.add_event(job["id"], "face_done", label)
            return out
        except Exception as e:
            log.warning("job=%s clip(s)=%s face pass failed, using Wan output: %s", job["id"], label, e)
            db.add_event(job["id"], "face_fail", f"{label}: {e}")
            return raw

    async def _cached(self, out: Path, cmd_for) -> Path:
        """Build a derived template file once (shared by every job), atomically."""
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.mp4")
            async with self.ffmpeg_slots:
                await fb.run(cmd_for(tmp))
            tmp.replace(out)
        return out

    async def _group_source(self, template: dict, group: list[str]) -> Path:
        """The template footage for a group: the clip itself, or a continuous group's clips joined end to end."""
        srcs = [config.template_clip(template, c) for c in group]
        if len(srcs) == 1:
            return srcs[0]
        key = f"{template['id']}_{'_'.join(str(p.stat().st_mtime_ns) for p in srcs)}"
        return await self._cached(config.DATA_DIR / "swap_inputs" / f"joined_{key}" / f"{group[0]}.mp4",
                                  lambda tmp: fb.concat_cmd(srcs, tmp))

    async def _swap_input(self, template: dict, group: list[str]) -> Path:
        """What Wan sees: the group's footage, cropped to the stand-in's region if it has one (file named after the
        group's first clip)."""
        src = await self._group_source(template, group)
        region = fb.swap_region(template, group[0])
        if not region:
            return src
        rx, rw = region_bounds(region)
        key = f"{template['id']}_{''.join(group)}_{rx}_{rw}_{src.stat().st_mtime_ns}"
        return await self._cached(config.DATA_DIR / "swap_inputs" / key / f"{group[0]}.mp4",
                                  lambda tmp: fb.swap_input_cmd(src, region, tmp))

    async def _finish_group(self, job: dict, group: list[str], raw: Path) -> dict[str, Path]:
        """Wan output -> one file per clip: blended back into the template (swap region), then split if continuous."""
        template = config.load_templates()[job["template_id"]]
        region = fb.swap_region(template, group[0])
        jd = job_dir(job["id"])
        joined = jd / f"{''.join(group)}.joined.mp4"
        if region:
            # Take only the pixels Wan changed (the visitor) and keep everything else from the template.
            src = await self._group_source(template, group)
            timed = jd / f"{''.join(group)}.timed.mp4"     # swap output on the template's clock (see retime_cmd)
            want, got = (await fb.probe(src))["duration"], (await fb.probe(raw))["duration"]
            async with self.ffmpeg_slots:
                await fb.run(fb.retime_cmd(raw, got, want, timed), job["id"])
                n = await asyncio.to_thread(composite.silhouette_composite, src, timed, region, joined, fb.FPS)
            log.info("job=%s clip(s)=%s silhouette composite: %s frames", job["id"], "".join(group), n)
        else:
            raw.replace(joined)
        if len(group) == 1:
            dest = jd / f"{group[0]}.mp4"
            joined.replace(dest)
            return {group[0]: dest}
        out, start = {}, 0.0
        for c in group:
            dest = jd / f"{c}.mp4"
            secs = fb.clip_seconds(template, c)
            async with self.ffmpeg_slots:
                await fb.run(fb.split_cmd(joined, start, secs, dest), job["id"])
            out[c] = dest
            start += secs
        return out

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
