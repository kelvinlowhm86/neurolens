"""
/api/jobs — create (reserve credits + presigned upload + pre-warm), poll, results, export, cancel.
"""

import csv
import io
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from flask import Blueprint, Response, current_app, request
from pydantic import BaseModel, Field, ValidationError

from neurolens_common import db, jobs, ledger, pricing, s3keys
from neurolens_common.config import Settings
from neurolens_common.jobs import JobStatus
from neurolens_common.ledger import InsufficientCredits
from neurolens_common.schemas import REGIONS

from . import warm_pool
from .auth import current_user_id, login_required
from .errors import error
from .services import Services

logger = logging.getLogger("neurolens.web.jobs")

bp = Blueprint("jobs", __name__)

EXT_FOR_TYPE = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}
MAX_CREATES_PER_MINUTE = 10

TIMING_FIELDS = ("created_at", "upload_confirmed_at", "s3_event_at", "claimed_at", "downloaded_at",
                 "validated_at", "pass_full_done_at", "pass_silent_done_at", "roi_done_at", "completed_at")


class CreateJob(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(gt=0)
    est_duration_s: float = Field(gt=0)


class RenderMetric(BaseModel):
    client_render_ms: int = Field(ge=0, le=600_000)


def _cfg() -> Settings:
    return current_app.extensions["nl_cfg"]


def _svc() -> Services:
    return current_app.extensions["nl"]


def _job_id(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


@bp.post("/api/jobs")
@login_required
def create_job():
    cfg, svc, uid = _cfg(), _svc(), current_user_id()
    try:
        body = CreateJob.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return error("INVALID_REQUEST", "Missing or invalid fields", 400, details=e.errors(include_url=False))

    if body.content_type not in cfg.allowed_types or body.content_type not in EXT_FOR_TYPE:
        return error("UNSUPPORTED_TYPE", "Upload an MP4, MOV or WebM video", 400)
    if body.size_bytes > cfg.max_upload_bytes:
        return error("TOO_LARGE", f"Videos must be under {cfg.max_upload_bytes // (1024 * 1024)} MB", 413)
    if body.est_duration_s > cfg.max_video_seconds:
        return error("TOO_LONG", f"Videos must be {cfg.max_video_seconds} seconds or shorter", 400)

    credits = pricing.credits_for_duration(body.est_duration_s, cfg)
    job_id = uuid4()
    key = s3keys.input_key(uid, job_id, EXT_FOR_TYPE[body.content_type])
    try:
        with db.transaction() as conn:
            if jobs.count_active_jobs(conn, uid) >= cfg.max_active_jobs_per_user:
                return error("TOO_MANY_ACTIVE", f"Up to {cfg.max_active_jobs_per_user} videos can be "
                             "in progress at once; wait for one to finish", 429)
            if jobs.count_recent_creates(conn, uid) >= MAX_CREATES_PER_MINUTE:
                return error("RATE_LIMITED", "Too many uploads in the last minute; try again shortly", 429)
            jobs.create_job(conn, job_id=job_id, user_id=uid, filename=body.filename,
                            content_type=body.content_type, size_bytes=body.size_bytes,
                            s3_input_key=key, est_duration_s=round(body.est_duration_s, 2))
            ledger.reserve(conn, job_id, credits)
            available = ledger.balance(conn, uid)["credits_available"]
    except InsufficientCredits:
        with db.transaction() as conn:
            available = ledger.balance(conn, uid)["credits_available"]
        return error("INSUFFICIENT_CREDITS", f"This video needs {credits} credits; you have {available}", 402,
                     needed=credits, available=available)

    upload = svc.storage.presigned_upload(key, body.content_type)

    # Wake a GPU now, in parallel with the upload. Never fails the request.
    try:
        with db.transaction() as conn:
            action = warm_pool.nudge(conn, cfg, svc)
            jobs.set_prewarm_action(conn, job_id, action)
            cap = warm_pool.capacity(conn, cfg, svc)
    except Exception:
        logger.exception("Pre-warm failed for job %s", job_id)
        action, cap = "error", {"workers_ready": 0, "workers_starting": 0}
    _note_job_created(cfg, svc)

    return {"job_id": job_id, "upload": upload, "credits_reserved": credits,
            "credits_available": available, "prewarm": action, "capacity": cap}, 201


def _note_job_created(cfg: Settings, svc: Services) -> None:
    """Tell the reaper there may be live jobs, so it does not skip the DB (lets Aurora pause when idle)."""
    if svc.ssm is None or not cfg.last_job_param:
        return
    try:
        svc.ssm.put_parameter(Name=cfg.last_job_param, Value=datetime.now(timezone.utc).isoformat(),
                              Type="String", Overwrite=True)
    except Exception:
        logger.warning("Could not update %s", cfg.last_job_param, exc_info=True)


@bp.post("/api/jobs/<raw_id>/uploaded")
@login_required
def mark_uploaded(raw_id):
    job_id = _job_id(raw_id)
    with db.transaction() as conn:
        job = jobs.get_job(conn, job_id, current_user_id()) if job_id else None
        if job is None:
            return error("NOT_FOUND", "No such job", 404)
        jobs.mark_uploaded(conn, job_id, current_user_id())
    return {"ok": True}


@bp.delete("/api/jobs/<raw_id>")
@login_required
def cancel(raw_id):
    job_id = _job_id(raw_id)
    with db.transaction() as conn:
        if job_id is None or jobs.get_job(conn, job_id, current_user_id()) is None:
            return error("NOT_FOUND", "No such job", 404)
        if not jobs.cancel_job(conn, job_id, current_user_id()):
            return error("NOT_CANCELLABLE", "Only jobs that have not been uploaded yet can be cancelled", 409)
    return {"ok": True}


@bp.get("/api/jobs/<raw_id>")
@login_required
def status(raw_id):
    cfg, svc = _cfg(), _svc()
    job_id = _job_id(raw_id)
    with db.transaction() as conn:
        job = jobs.get_job(conn, job_id, current_user_id()) if job_id else None
        if job is None:
            return error("NOT_FOUND", "No such job", 404)
        position = jobs.queue_position(conn, job)
        cap = warm_pool.capacity(conn, cfg, svc) if job["status"] not in jobs.TERMINAL else None
    return {
        "job_id": job["id"],
        "status": job["status"],
        "filename": job["original_filename"],
        "progress_stage": job["progress_stage"],
        "progress_pct": job["progress_pct"],
        "queue_position": position,
        "capacity": cap,
        "attempts": job["attempts"],
        "credits_reserved": job["credits_reserved"],
        "credits_captured": job["credits_captured"],
        "duration_s": job["actual_duration_s"] or job["est_duration_s"],
        "error": {"code": job["error_code"], "message": job["error_message"]} if job["error_code"] else None,
        "report": {"status": job["report_status"], "error": job["report_error"], "model": job["report_model"]},
        "timings": {k: job[k] for k in TIMING_FIELDS},
    }


def _succeeded_job(raw_id):
    job_id = _job_id(raw_id)
    with db.transaction() as conn:
        job = jobs.get_job(conn, job_id, current_user_id()) if job_id else None
    if job is None:
        return None, error("NOT_FOUND", "No such job", 404)
    if job["status"] != JobStatus.SUCCEEDED:
        return None, error("NOT_READY", f"Job is {job['status']}", 409)
    return job, None


@bp.get("/api/jobs/<raw_id>/result")
@login_required
def result(raw_id):
    job, err = _succeeded_job(raw_id)
    if err:
        return err
    storage = _svc().storage
    return {"result": storage.read_json(job["s3_result_key"]),
            "video_url": storage.presigned_get(job["s3_input_key"])}


@bp.get("/api/jobs/<raw_id>/export.csv")
@login_required
def export_csv(raw_id):
    job, err = _succeeded_job(raw_id)
    if err:
        return err
    res = _svc().storage.read_json(job["s3_result_key"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["t", "engagement_overall", *REGIONS, "auditory_with_audio", "auditory_without_audio",
                *(f"raw_{r}" for r in REGIONS)])
    raw = res.get("raw", {})
    for i, ts in enumerate(res["timesteps"]):
        w.writerow([ts["t"], ts["engagement_overall"], *(ts["regions"][r] for r in REGIONS),
                    ts["auditory_with_audio"], ts["auditory_without_audio"],
                    *(raw.get(r, [None] * (i + 1))[i] for r in REGIONS)])
    stem = job["original_filename"].rsplit(".", 1)[0][:60] or "neurolens"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{stem}-neurolens.csv"'})


@bp.get("/api/jobs/<raw_id>/report.pdf")
@login_required
def report_pdf(raw_id):
    """The AI report. Inline so the dashboard can show it in place; ?download=1 saves it instead."""
    job, err = _succeeded_job(raw_id)
    if err:
        return err
    if job["report_status"] != "READY" or not job["s3_report_key"]:
        return error("REPORT_NOT_READY", f"Report is {(job['report_status'] or 'not requested').lower()}", 409)
    body = _svc().storage.read_bytes(job["s3_report_key"])
    stem = job["original_filename"].rsplit(".", 1)[0][:60] or "neurolens"
    disposition = "attachment" if request.args.get("download") else "inline"
    return Response(body, mimetype="application/pdf", headers={
        "Content-Disposition": f'{disposition}; filename="{stem}-neurolens-report.pdf"',
        "Cache-Control": "private, max-age=300",
    })


@bp.post("/api/jobs/<raw_id>/metrics")
@login_required
def render_metric(raw_id):
    job_id = _job_id(raw_id)
    try:
        body = RenderMetric.model_validate(request.get_json(silent=True) or {})
    except ValidationError:
        return error("INVALID_REQUEST", "client_render_ms must be a number of milliseconds", 400)
    if job_id is None:
        return error("NOT_FOUND", "No such job", 404)
    with db.transaction() as conn:
        jobs.record_client_render(conn, job_id, current_user_id(), body.client_render_ms)
    return {"ok": True}


@bp.get("/api/jobs")
@login_required
def history():
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 100))
        before = datetime.fromisoformat(request.args["before"]) if "before" in request.args else None
    except ValueError:
        return error("INVALID_REQUEST", "limit must be a number and before an ISO timestamp", 400)
    with db.transaction() as conn:
        rows = jobs.list_jobs(conn, current_user_id(), limit, before)
    return {"jobs": rows}
