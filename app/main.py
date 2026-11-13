"""FastAPI app: operator pages (PIN session), JSON API, and the public result page."""
import asyncio
import hashlib
import hmac
import html
import io
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db
from .photo_check import PhotoRejected, check_and_prepare
from .worker import Worker, job_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")

PAGES = config.ROOT / "templates"
COOKIE = "lwm_session"
worker: Worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    problems = config.validate()
    if problems:
        raise RuntimeError("Cannot start:\n  - " + "\n  - ".join(problems))
    db.init()
    worker = Worker()
    worker.resume()
    log.info("started (MOCK_WAN=%s, wan slots=%s, ffmpeg slots=%s)",
             config.MOCK_WAN, config.MAX_CONCURRENT_WAN_JOBS, config.MAX_CONCURRENT_FFMPEG)
    yield
    await worker.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if not request.url.path.startswith(("/static", "/media", "/qr", "/poster", "/brand")):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---- auth -------------------------------------------------------------------------------------
def _secret() -> bytes:
    if config.SESSION_SECRET:
        return config.SESSION_SECRET.encode()
    path = config.DATA_DIR / "session.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip().encode()


def _sign(expires: int) -> str:
    # The PIN is part of the key, so changing OPERATOR_PIN logs every device out.
    key = _secret() + config.OPERATOR_PIN.encode()
    return hmac.new(key, f"operator:{expires}".encode(), hashlib.sha256).hexdigest()


def is_operator(request: Request) -> bool:
    raw = request.cookies.get(COOKIE, "")
    expires, _, sig = raw.partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(sig, _sign(int(expires)))


def operator_api(request: Request) -> None:
    if not is_operator(request):
        raise HTTPException(401, "Not logged in. Open /login and enter the operator PIN.")


class NeedLogin(Exception):
    def __init__(self, next_path: str):
        self.next_path = next_path


@app.exception_handler(NeedLogin)
async def _need_login(request: Request, exc: NeedLogin):
    return RedirectResponse(f"/login?next={exc.next_path}", status_code=303)


def operator_page(request: Request) -> None:
    if not is_operator(request):
        raise NeedLogin(request.url.path)


_failed_logins: dict[str, list[float]] = {}


@app.get("/login", response_class=HTMLResponse)
async def login_page(next: str = "/capture", error: str = ""):
    return page("login.html", next=_safe_next(next), error=error)


@app.post("/login")
async def login(request: Request, pin: str = Form(""), next: str = Form("/capture")):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _failed_logins.get(ip, []) if t > time.time() - 300]
    if len(recent) >= 5:
        return RedirectResponse("/login?error=Too+many+attempts.+Wait+5+minutes.", status_code=303)
    if not hmac.compare_digest(pin.strip().encode(), config.OPERATOR_PIN.encode()):
        _failed_logins[ip] = recent + [time.time()]
        await asyncio.sleep(1)
        return RedirectResponse(f"/login?next={_safe_next(next)}&error=Wrong+PIN", status_code=303)
    _failed_logins.pop(ip, None)
    expires = int(time.time()) + config.SESSION_HOURS * 3600
    resp = RedirectResponse(_safe_next(next), status_code=303)
    resp.set_cookie(COOKIE, f"{expires}.{_sign(expires)}", max_age=config.SESSION_HOURS * 3600, httponly=True,
                    samesite="lax", secure=config.BASE_URL.startswith("https://"))
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


def _safe_next(path: str) -> str:
    return path if re.fullmatch(r"/[A-Za-z0-9/_-]*", path or "") else "/capture"


# ---- pages ------------------------------------------------------------------------------------
def page(template: str, /, **values: str) -> HTMLResponse:
    """Tiny templating: {{key}} -> HTML-escaped value. Keys ending in _json are inserted raw (already JSON)."""
    text = (PAGES / template).read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value if key.endswith("_json") else html.escape(str(value)))
    return HTMLResponse(text)


def _json_for_script(obj) -> str:
    return json.dumps(obj).replace("<", "\\u003c")


@app.get("/", response_class=HTMLResponse)
async def landing():
    return page("landing.html")


BRAND_FILES = {"logo.png": "image/png", "medallion.jpg": "image/jpeg"}


@app.get("/brand/{name}")
async def brand(name: str):
    """Public brand images for the site (built by scripts/make_brand.py into assets/brand/)."""
    path = config.ASSETS_DIR / "brand" / name
    if name not in BRAND_FILES or not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type=BRAND_FILES[name], headers={"Cache-Control": "public, max-age=86400"})


@app.get("/capture", response_class=HTMLResponse, dependencies=[Depends(operator_page)])
async def capture_page():
    templates = [{"id": t["id"], "label": t["label"]} for t in config.load_templates().values()]
    return page("capture.html", templates_json=_json_for_script(templates))


@app.get("/qc", response_class=HTMLResponse, dependencies=[Depends(operator_page)])
async def qc_page():
    return page("qc.html")


@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(operator_page)])
async def admin_page():
    return page("admin.html", stats_json=_json_for_script(_stats()), mock=str(config.MOCK_WAN).lower(),
                wan_slots=str(config.MAX_CONCURRENT_WAN_JOBS), ffmpeg_slots=str(config.MAX_CONCURRENT_FFMPEG))


@app.get("/admin/stats", dependencies=[Depends(operator_api)])
async def admin_stats():
    return _stats()


def _stats() -> dict:
    return {**db.stats(), "running_jobs": sum(worker.is_running(j) for j in list(worker.tasks))}


# ---- jobs API ---------------------------------------------------------------------------------
def _job_json(job: dict, with_events: bool = False) -> dict:
    clips = db.get_clips(job["id"])
    out = {
        "id": job["id"], "ticket": job["ticket"], "name": job["name"], "phone": job["phone"],
        "template_id": job["template_id"], "status": job["status"], "error": job["error"],
        "consent_at": job["consent_at"], "created_at": job["created_at"], "updated_at": job["updated_at"],
        "running": worker.is_running(job["id"]),
        "clips": {c: {"status": r["status"], "attempts": r["attempts"], "error": r["error"]} for c, r in clips.items()},
        "photo_url": f"/jobs/{job['id']}/photo.jpg",
        "video_url": f"/jobs/{job['id']}/video.mp4?v={job['updated_at']}" if job["video_path"] else None,
    }
    if job["status"] == "approved":
        out |= _public_urls(job)
    if with_events:
        out["events"] = db.job_events(job["id"])
    return out


def _public_urls(job: dict) -> dict:
    return {"result_url": f"{config.BASE_URL}/v/{job['token']}", "qr_png_url": f"/qr/{job['token']}.png"}


def _get_job(job_id: int) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


@app.post("/jobs", dependencies=[Depends(operator_api)])
async def create_job(
    name: str = Form(...), phone: str = Form(...), consent: str = Form(""), template_id: str = Form(...),
    photo: UploadFile = File(...),
):
    name = re.sub(r"\s+", " ", "".join(ch for ch in name if ch.isprintable())).strip()
    phone = phone.strip()
    if not 1 <= len(name) <= 40:
        raise HTTPException(400, "Enter the visitor's name (up to 40 characters).")
    if not re.fullmatch(r"\+?[0-9 ()-]{7,20}", phone):
        raise HTTPException(400, "Enter a valid phone number (digits, optional + at the start).")
    if consent.lower() not in ("true", "on", "1", "yes"):
        raise HTTPException(400, "The visitor must agree to the consent statement before we continue.")
    if template_id not in config.load_templates():
        raise HTTPException(400, f"Unknown template '{template_id}'.")
    data = await photo.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"Photo is larger than {config.MAX_UPLOAD_MB} MB. Retake at normal quality.")
    if not data:
        raise HTTPException(400, "No photo received. Take the photo and try again.")

    staging = config.DATA_DIR / "staging" / secrets.token_hex(8)
    staging.mkdir(parents=True)
    try:
        info = await asyncio.to_thread(check_and_prepare, data, staging / "original.jpg", staging / "photo.jpg")
    except PhotoRejected as e:
        _rmtree(staging)
        raise HTTPException(400, str(e))

    job = db.create_job(name, phone, template_id, photo_path="")
    d = job_dir(job["id"])
    (staging / "original.jpg").rename(d / "original.jpg")
    (staging / "photo.jpg").rename(d / "photo.jpg")
    _rmtree(staging)
    db.update_job(job["id"], photo_path=str(d / "photo.jpg"))
    db.add_event(job["id"], "created", f"template={template_id} photo={info}")
    log.info("job=%s created ticket=%s template=%s photo=%s", job["id"], job["ticket"], template_id, info)
    worker.start(job["id"])
    return {"job_id": job["id"], "ticket": job["ticket"]}


def _rmtree(path: Path) -> None:
    for p in path.glob("*"):
        p.unlink()
    path.rmdir()


@app.get("/jobs", dependencies=[Depends(operator_api)])
async def list_jobs(limit: int = 200):
    return {"jobs": [_job_json(j) for j in db.list_jobs(min(limit, 500))]}


@app.get("/jobs/{job_id}", dependencies=[Depends(operator_api)])
async def job_status(job_id: int):
    return _job_json(_get_job(job_id), with_events=True)


@app.get("/jobs/{job_id}/video.mp4", dependencies=[Depends(operator_api)])
async def job_video(job_id: int):
    job = _get_job(job_id)
    if not job["video_path"] or not Path(job["video_path"]).exists():
        raise HTTPException(404, "No video yet")
    return FileResponse(job["video_path"], media_type="video/mp4")


@app.get("/jobs/{job_id}/photo.jpg", dependencies=[Depends(operator_api)])
async def job_photo(job_id: int):
    job = _get_job(job_id)
    if not job["photo_path"] or not Path(job["photo_path"]).exists():
        raise HTTPException(404, "No photo")
    return FileResponse(job["photo_path"], media_type="image/jpeg")


@app.post("/jobs/{job_id}/approve", dependencies=[Depends(operator_api)])
async def approve(job_id: int):
    job = _get_job(job_id)
    if job["status"] != "approved":
        if job["status"] != "ready_for_qc":
            raise HTTPException(409, f"Only videos ready for QC can be approved (this job is {job['status']}).")
        db.update_job(job_id, status="approved")
        db.add_event(job_id, "approved")
        log.info("job=%s approved", job_id)
    return _public_urls(job)


@app.post("/jobs/{job_id}/rerun/{clip}", dependencies=[Depends(operator_api)])
async def rerun(job_id: int, clip: str):
    clip = clip.upper()
    if clip not in config.CLIPS:
        raise HTTPException(400, "Clip must be A, B or C.")
    job = _get_job(job_id)
    if worker.is_running(job_id) or job["status"] in db.ACTIVE_JOB_STATES:
        raise HTTPException(409, "This job is still processing. Wait until it is ready or failed, then rerun.")
    if job["status"] not in ("ready_for_qc", "failed"):
        raise HTTPException(409, f"Can't rerun a job that is {job['status']}.")
    db.update_clip(job_id, clip, status="pending", attempts=0, provider_task_id=None, error=None)
    db.update_job(job_id, status="queued", error=None)
    db.add_event(job_id, "rerun", clip)
    log.info("job=%s rerun clip %s", job_id, clip)
    worker.start(job_id)
    return {"ok": True, "status": "queued"}


@app.post("/jobs/{job_id}/reject", dependencies=[Depends(operator_api)])
async def reject(job_id: int):
    _get_job(job_id)
    db.update_job(job_id, status="rejected")  # set first: the worker checks it before every API call
    worker.cancel(job_id)
    db.add_event(job_id, "rejected")
    log.info("job=%s rejected", job_id)
    return {"ok": True, "status": "rejected"}


# ---- public -----------------------------------------------------------------------------------
def _approved(token: str) -> dict:
    job = db.get_job_by_token(token) if re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token) else None
    if not job or job["status"] != "approved" or not job["video_path"]:
        raise HTTPException(404, "Video not found")
    return job


@app.get("/v/{token}", response_class=HTMLResponse)
async def result_page(token: str):
    job = _approved(token)
    return page("result.html", name=job["name"], token=token, url=f"{config.BASE_URL}/v/{token}",
                base_url=config.BASE_URL)


@app.get("/media/{token}.mp4")
async def media(token: str, download: int = 0):
    job = _approved(token)
    filename = "loveworld-moment-" + (re.sub(r"[^A-Za-z0-9]+", "-", job["name"]).strip("-").lower() or "video") + ".mp4"
    return FileResponse(job["video_path"], media_type="video/mp4",
                        filename=filename if download else None,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, max-age=3600"})


@app.get("/poster/{token}.jpg")
async def poster(token: str):
    job = _approved(token)
    path = Path(job["video_path"]).with_name("poster.jpg")
    if not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/qr/{token}.png")
async def qr_png(token: str):
    job = _approved(token)
    img = qrcode.make(f"{config.BASE_URL}/v/{job['token']}", box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/v/"):
        resp = page("not_found.html")
        resp.status_code = 404
        return resp
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": f"Server error: {exc}"}, status_code=500)
