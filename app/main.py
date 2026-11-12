"""FastAPI app: the /api routes of PLAN.md section 4.7 plus the web UI files.

create_app(data_dir) builds a fresh app on one data folder (tests use a
temporary folder). `app` (module level) is made on first use from
NEST_DATA_DIR, so importing this module does not touch ./data.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import backup as backup_mod
from . import services as sv
from .db import Database, default_data_dir, get_database, session_scope
from .logs import new_request_id, request_id, setup_logging
from .models import FeedOverride, Job, JobPart, Machine, Material, Offcut, ReliefRun, Task, Tool
from .schemas import (BackupOut, ChecksOut, ChecksQuery, CommitOut, ExportIn, ExportOut, FeedIn, FeedOut,
                      FeedUpdate, JobCreate, JobDetail, JobListItem, JobUpdate,
                      MachineIn, MachineOut, MachineUpdate, MaterialIn, MaterialOut, MaterialUpdate, NestIn,
                      NestOut, OffcutCreate, OffcutOut, OffcutUploadOut, PartOut, PartUpdate, ReliefIn, ReliefOut,
                      TaskOut, ToolIn, ToolOut, ToolUpdate, UploadOut)
from .seed import seed
from .tasks import Worker

log = logging.getLogger("nest")
Async = Annotated[bool, Query(alias="async", description="1 = run in the background, return {task_id}")]

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


# ---------- dependencies ----------

def get_db(request: Request) -> Database:
    return request.app.state.db


def get_session(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.db)


def data_dir_of(request: Request) -> str:
    return request.app.state.db.data_dir


S = Annotated[Session, Depends(get_session)]
D = Annotated[str, Depends(data_dir_of)]


def _get(s: Session, model, id_, what: str):
    row = s.get(model, id_)
    if row is None:
        raise sv.not_found(what)
    return row


def _apply(row, changes: dict) -> None:
    for k, v in changes.items():
        setattr(row, k, v)


def _job(s: Session, jid: int, binned: bool = False) -> Job:
    """The job, 404 when missing. Jobs in the bin are only found with binned=True."""
    j = s.get(Job, jid)
    if j is None or (j.deleted_at is not None and not binned):
        raise sv.not_found("Job")
    return j


def _open_job(s: Session, jid: int) -> Job:
    """A job that may be changed (not in the bin, not locked)."""
    j = _job(s, jid)
    sv.ensure_unlocked(j)
    return j


def _worker(request: Request) -> Worker:
    return request.app.state.worker


def _task_response(s: Session, t: Task, status: int = 202) -> JSONResponse:
    s.expire_all()
    t = s.get(Task, t.id)
    return JSONResponse(sv.task_out(t), status_code=status)


# ---------- routes ----------

def build_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health")
    def health(request: Request):
        db: Database = request.app.state.db
        db_ok, queued = True, 0
        try:
            with db.session() as s:
                s.execute(text("SELECT 1"))
                queued = s.scalar(select(func.count()).select_from(Task).where(Task.status == "queued")) or 0
        except Exception:  # noqa: BLE001
            log.exception("health: database check failed")
            db_ok = False
        try:
            free = shutil.disk_usage(db.data_dir).free
        except OSError:
            free = None
        last = backup_mod.last_backup(db.data_dir)
        w = request.app.state.worker
        return {"ok": db_ok, "version": sv.VERSION, "db_ok": db_ok,
                "disk_free_mb": round(free / 1e6) if free is not None else None,
                "uptime_s": round(time.time() - request.app.state.started_at, 1),
                "last_backup": last.get("time") if last else None,
                "worker": {"running": bool(w.started), "queued": int(queued)}}

    # --- machines ---
    @r.get("/machines", response_model=list[MachineOut])
    def list_machines(s: S):
        return [sv.machine_out(m) for m in s.scalars(select(Machine).order_by(Machine.id))]

    @r.post("/machines", response_model=MachineOut, status_code=201)
    def create_machine(body: MachineIn, s: S):
        m = Machine(**body.model_dump())
        s.add(m)
        s.commit()
        return sv.machine_out(m)

    @r.get("/machines/{mid}", response_model=MachineOut)
    def get_machine(mid: int, s: S):
        return sv.machine_out(_get(s, Machine, mid, "Machine"))

    @r.put("/machines/{mid}", response_model=MachineOut)
    def update_machine(mid: int, body: MachineUpdate, s: S):
        m = _get(s, Machine, mid, "Machine")
        _apply(m, body.model_dump(exclude_unset=True, exclude_none=True))
        s.commit()
        return sv.machine_out(m)

    @r.delete("/machines/{mid}", status_code=204)
    def delete_machine(mid: int, s: S):
        m = _get(s, Machine, mid, "Machine")
        for j in s.scalars(select(Job).where(Job.machine_id == mid)):
            j.machine_id = None
        s.delete(m)
        s.commit()
        return Response(status_code=204)

    # --- tools ---
    @r.get("/tools", response_model=list[ToolOut])
    def list_tools(s: S):
        return [sv.tool_out(t) for t in s.scalars(select(Tool).order_by(Tool.id))]

    @r.post("/tools", response_model=ToolOut, status_code=201)
    def create_tool(body: ToolIn, s: S):
        t = Tool(**body.model_dump())
        s.add(t)
        s.commit()
        return sv.tool_out(t)

    @r.get("/tools/{tid}", response_model=ToolOut)
    def get_tool(tid: int, s: S):
        return sv.tool_out(_get(s, Tool, tid, "Tool"))

    @r.put("/tools/{tid}", response_model=ToolOut)
    def update_tool(tid: int, body: ToolUpdate, s: S):
        t = _get(s, Tool, tid, "Tool")
        changes = body.model_dump(exclude_unset=True)
        changes = {k: v for k, v in changes.items() if v is not None or k == "angle_deg"}
        _apply(t, changes)
        s.commit()
        return sv.tool_out(t)

    @r.delete("/tools/{tid}", status_code=204)
    def delete_tool(tid: int, s: S):
        t = _get(s, Tool, tid, "Tool")
        for f in s.scalars(select(FeedOverride).where(FeedOverride.tool_id == tid)):
            s.delete(f)
        s.delete(t)
        s.commit()
        return Response(status_code=204)

    # --- materials ---
    @r.get("/materials", response_model=list[MaterialOut])
    def list_materials(s: S):
        return [sv.material_out(m) for m in s.scalars(select(Material).order_by(Material.id))]

    @r.post("/materials", response_model=MaterialOut, status_code=201)
    def create_material(body: MaterialIn, s: S):
        m = Material(**body.model_dump())
        s.add(m)
        s.commit()
        return sv.material_out(m)

    @r.get("/materials/{mid}", response_model=MaterialOut)
    def get_material(mid: int, s: S):
        return sv.material_out(_get(s, Material, mid, "Material"))

    @r.put("/materials/{mid}", response_model=MaterialOut)
    def update_material(mid: int, body: MaterialUpdate, s: S):
        m = _get(s, Material, mid, "Material")
        _apply(m, body.model_dump(exclude_unset=True, exclude_none=True))
        s.commit()
        return sv.material_out(m)

    @r.delete("/materials/{mid}", status_code=204)
    def delete_material(mid: int, s: S):
        m = _get(s, Material, mid, "Material")
        for j in s.scalars(select(Job).where(Job.material_id == mid)):
            j.material_id = None
        for o in s.scalars(select(Offcut).where(Offcut.material_id == mid)):
            s.delete(o)
        for f in s.scalars(select(FeedOverride).where(FeedOverride.material_id == mid)):
            s.delete(f)
        s.delete(m)
        s.commit()
        return Response(status_code=204)

    # --- feeds and speeds (material x bit overrides) ---
    @r.get("/feeds", response_model=list[FeedOut])
    def list_feeds(s: S, material_id: int | None = None, tool_id: int | None = None):
        q = select(FeedOverride).order_by(FeedOverride.material_id, FeedOverride.tool_id)
        if material_id is not None:
            q = q.where(FeedOverride.material_id == material_id)
        if tool_id is not None:
            q = q.where(FeedOverride.tool_id == tool_id)
        return [sv.feed_out(f) for f in s.scalars(q)]

    @r.post("/feeds", response_model=FeedOut, status_code=201)
    def create_feed(body: FeedIn, s: S):
        _get(s, Material, body.material_id, "Material")
        _get(s, Tool, body.tool_id, "Tool")
        if sv.feed_for(s, body.material_id, body.tool_id) is not None:
            raise sv.UserError("This material and bit already have feeds and speeds: edit that row", 409)
        f = FeedOverride(**body.model_dump())
        s.add(f)
        s.commit()
        return sv.feed_out(f)

    @r.get("/feeds/{fid}", response_model=FeedOut)
    def get_feed(fid: int, s: S):
        return sv.feed_out(_get(s, FeedOverride, fid, "Feeds row"))

    @r.put("/feeds/{fid}", response_model=FeedOut)
    def update_feed(fid: int, body: FeedUpdate, s: S):
        f = _get(s, FeedOverride, fid, "Feeds row")
        changes = body.model_dump(exclude_unset=True)
        if changes.get("notes", "") is None:
            changes["notes"] = ""
        _apply(f, changes)           # a value sent as null clears that override
        s.commit()
        return sv.feed_out(f)

    @r.delete("/feeds/{fid}", status_code=204)
    def delete_feed(fid: int, s: S):
        s.delete(_get(s, FeedOverride, fid, "Feeds row"))
        s.commit()
        return Response(status_code=204)

    # --- offcuts ---
    @r.get("/offcuts", response_model=list[OffcutOut])
    def list_offcuts(s: S, material_id: int | None = None, used: bool | None = None):
        q = select(Offcut).order_by(Offcut.id.desc())
        if material_id is not None:
            q = q.where(Offcut.material_id == material_id)
        if used is not None:
            q = q.where(Offcut.used.is_(used))
        return [sv.offcut_out(o) for o in s.scalars(q)]

    @r.post("/offcuts", response_model=OffcutOut, status_code=201)
    def create_offcut(body: OffcutCreate, s: S):
        return sv.offcut_out(sv.create_offcut(s, body.material_id, body.width, body.height, body.name))

    @r.post("/offcuts/upload", response_model=OffcutUploadOut, status_code=201)
    def upload_offcut(s: S, material_id: int = Form(...), file: UploadFile = File(...)):
        o, warnings = sv.upload_offcut(s, material_id, file)
        return {**sv.offcut_out(o), "warnings": warnings}

    @r.delete("/offcuts/{oid}", status_code=204)
    def delete_offcut(oid: int, s: S):
        s.delete(_get(s, Offcut, oid, "Offcut"))
        s.commit()
        return Response(status_code=204)

    # --- jobs ---
    def _check_links(s: Session, material_id, machine_id) -> None:
        if material_id is not None and s.get(Material, material_id) is None:
            raise sv.UserError("Material not found")
        if machine_id is not None and s.get(Machine, machine_id) is None:
            raise sv.UserError("Machine not found")

    @r.get("/jobs", response_model=list[JobListItem])
    def list_jobs(s: S, bin: bool = False):  # noqa: A002  (?bin=1 lists the bin)
        mats = {m.id: m for m in s.scalars(select(Material))}
        machs = {m.id: m for m in s.scalars(select(Machine))}
        q = select(Job).where(Job.deleted_at.is_not(None) if bin else Job.deleted_at.is_(None))
        jobs = s.scalars(q.order_by(Job.deleted_at.desc() if bin else Job.created_at.desc(), Job.id.desc()))
        return [sv.job_list_item(s, j, mats, machs) for j in jobs]

    @r.post("/jobs", response_model=JobDetail, status_code=201)
    def create_job(body: JobCreate, s: S):
        _check_links(s, body.material_id, body.machine_id)
        j = Job(name=body.name.strip() or "Job", material_id=body.material_id, machine_id=body.machine_id,
                status="draft")
        s.add(j)
        s.commit()
        return sv.job_detail(s, j)

    @r.get("/jobs/{jid}", response_model=JobDetail)
    def get_job(jid: int, s: S):
        return sv.job_detail(s, _job(s, jid))

    @r.patch("/jobs/{jid}", response_model=JobDetail)
    def update_job(jid: int, body: JobUpdate, s: S):
        j = _job(s, jid)
        changes = body.model_dump(exclude_unset=True)
        if changes.get("name") is None:
            changes.pop("name", None)
        if any(changes.get(k, getattr(j, k)) != getattr(j, k) for k in ("material_id", "machine_id")):
            sv.ensure_unlocked(j)              # the name of a cut job may change, nothing else
        _check_links(s, changes.get("material_id"), changes.get("machine_id"))
        _apply(j, changes)
        s.commit()
        return sv.job_detail(s, j)

    @r.delete("/jobs/{jid}", status_code=204)
    def delete_job(jid: int, s: S, d: D, purge: bool = False):
        """Moves the job to the bin; ?purge=1 on a job in the bin deletes it for good."""
        j = _job(s, jid, binned=True)
        if purge:
            if j.deleted_at is None:
                raise sv.UserError("Move the job to the bin before deleting it for good", 409)
            sv.purge_job(s, d, j)
        elif j.deleted_at is not None:
            raise sv.not_found("Job")
        else:
            sv.delete_job(s, d, j)
        return Response(status_code=204)

    @r.post("/jobs/{jid}/restore", response_model=JobDetail)
    def restore_job(jid: int, s: S):
        j = _job(s, jid, binned=True)
        sv.restore_job(s, j)
        return sv.job_detail(s, j)

    @r.post("/jobs/{jid}/duplicate", response_model=JobDetail, status_code=201)
    def duplicate_job(jid: int, s: S):
        return sv.job_detail(s, sv.duplicate_job(s, _job(s, jid)))

    @r.post("/jobs/{jid}/unlock", response_model=JobDetail)
    def unlock_job(jid: int, s: S):
        j = _job(s, jid)
        sv.unlock_job(s, j)
        return sv.job_detail(s, j)

    @r.post("/jobs/{jid}/files", response_model=UploadOut)
    def upload_files(jid: int, s: S, file: list[UploadFile] = File(...)):
        return sv.import_files(s, _open_job(s, jid), file)

    def _part_row(s: Session, jid: int, pid: str) -> JobPart:
        _open_job(s, jid)
        row = s.scalars(select(JobPart).where(JobPart.job_id == jid, JobPart.part_id == pid)).first()
        if row is None:
            raise sv.not_found("Part")
        return row

    @r.patch("/jobs/{jid}/parts/{pid}", response_model=PartOut)
    def update_part(jid: int, pid: str, body: PartUpdate, s: S):
        row = _part_row(s, jid, pid)
        _apply(row, body.model_dump(exclude_unset=True, exclude_none=True))
        s.commit()
        return sv.part_out(sv.part_from_row(row))

    @r.delete("/jobs/{jid}/parts/{pid}", status_code=204)
    def delete_part(jid: int, pid: str, s: S):
        s.delete(_part_row(s, jid, pid))
        s.commit()
        return Response(status_code=204)

    @r.post("/jobs/{jid}/nest", response_model=NestOut,
            responses={202: {"model": TaskOut, "description": "?async=1: the queued task"}})
    def nest_job(jid: int, request: Request, s: S, d: D, body: NestIn | None = None, run_async: Async = False):
        j = _open_job(s, jid)
        body = body or NestIn()
        busy = sv.active_task(s, "nest", jid)
        if busy is not None:
            raise sv.UserError("This job is already being nested", 409, "task_busy")
        if not run_async:
            return sv.run_nest(s, d, j, body)
        sv.prepare_nest(s, j, body)            # bad input fails now, not in the background
        t = _worker(request).submit("nest", body.model_dump(exclude_none=True), job_id=jid)
        return _task_response(s, t)

    @r.post("/jobs/{jid}/export", response_model=ExportOut)
    def export_job(jid: int, body: ExportIn, s: S, d: D):
        return sv.run_export(s, d, _job(s, jid), body)

    @r.get("/jobs/{jid}/checks", response_model=ChecksOut)
    def job_checks(jid: int, s: S, q: Annotated[ChecksQuery, Query()]):
        return sv.run_checks(s, _job(s, jid), q)

    @r.get("/jobs/{jid}/export.zip")
    def export_zip(jid: int, s: S, d: D, all: bool = False):
        j = _job(s, jid)
        data = sv.export_zip(d, j, all_revisions=all)
        name = sv.slug(j.name, f"job{j.id}") + ".zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @r.get("/jobs/{jid}/files/{name}")
    def job_file(jid: int, name: str, s: S, d: D):
        path = sv.job_file_path(d, _job(s, jid), name)
        return FileResponse(path, filename=name, media_type=_media_type(name))

    @r.post("/jobs/{jid}/commit", response_model=CommitOut)
    def commit_job(jid: int, s: S):
        j = _job(s, jid)
        if sv.active_task(s, "nest", jid) is not None:
            raise sv.UserError("This job is being nested: wait for the nest to finish", 409, "task_busy")
        saved, used = sv.commit_job(s, j)
        return {**sv.job_detail(s, j), "offcuts_saved": [sv.offcut_out(o) for o in saved],
                "offcuts_used": used}

    # --- relief ---
    @r.post("/relief", response_model=ReliefOut,
            responses={202: {"model": TaskOut, "description": "?async=1: the queued task (ref = relief id)"}})
    def create_relief(
        request: Request, s: S, d: D,
        run_async: Async = False,
        image: UploadFile = File(...),
        width_mm: float = Form(...),
        material_mm: float = Form(...),
        max_height_mm: float | None = Form(None),
        max_colors: int | None = Form(None),
        resolution_mm: float | None = Form(None),
        rough: str | None = Form(None),
        finish: str | None = Form(None),
        rough_feed: float | None = Form(None),
        finish_feed: float | None = Form(None),
        plunge_feed: float | None = Form(None),
        rpm: int | None = Form(None),
        stepdown_mm: float | None = Form(None),
        allowance_mm: float | None = Form(None),
        safe_z: float | None = Form(None),
        styles: str | None = Form(None),
    ):
        raw = dict(width_mm=width_mm, material_mm=material_mm, max_height_mm=max_height_mm,
                   max_colors=max_colors, resolution_mm=resolution_mm, rough=rough, finish=finish,
                   rough_feed=rough_feed, finish_feed=finish_feed, plunge_feed=plunge_feed, rpm=rpm,
                   stepdown_mm=stepdown_mm, allowance_mm=allowance_mm, safe_z=safe_z)
        if styles not in (None, ""):
            try:
                raw["styles"] = json.loads(styles)
            except ValueError as e:
                raise sv.UserError("styles must be JSON (a list of region settings)") from e
        try:
            params = ReliefIn(**raw).model_dump(exclude_none=True)
        except ValueError as e:
            raise sv.UserError(_first_error(e)) from e
        if not run_async:
            return sv.create_relief(s, d, image, params)
        run = sv.store_relief_upload(d, image, params)
        s.add(run)
        s.commit()
        t = _worker(request).submit("relief", {"create": True}, ref=run.id)
        return _task_response(s, t)

    def _relief_ready(s: Session, rid: str) -> ReliefRun:
        run = _get(s, ReliefRun, rid, "Relief")
        if sv.active_task(s, "relief", ref=rid) is not None or run.result_json in (None, "", "{}"):
            raise sv.UserError("This relief is still being made", 409, "task_busy")
        return run

    @r.get("/relief/{rid}", response_model=ReliefOut)
    def get_relief(rid: str, s: S):
        return sv.relief_out(_relief_ready(s, rid))

    @r.post("/relief/{rid}", response_model=ReliefOut,
            responses={202: {"model": TaskOut, "description": "?async=1: the queued task"}})
    def rerun_relief(rid: str, body: ReliefIn, request: Request, s: S, d: D, run_async: Async = False):
        run = _relief_ready(s, rid)
        changes = body.model_dump(exclude_unset=True, exclude_none=True)
        if not run_async:
            return sv.rerun_relief(s, d, run, changes)
        t = _worker(request).submit("relief", {"create": False, "changes": changes}, ref=rid)
        return _task_response(s, t)

    @r.get("/relief/{rid}/files/{name}")
    def relief_file(rid: str, name: str, s: S, d: D):
        if not sv.is_safe_name(rid):
            raise sv.UserError("Bad id")
        _get(s, ReliefRun, rid, "Relief")
        path = sv.safe_file(sv.relief_dir(d, rid), name)
        return FileResponse(path, filename=name, media_type=_media_type(name))

    # --- background tasks ---
    @r.get("/tasks", response_model=list[TaskOut])
    def list_tasks(s: S, job_id: int | None = None, kind: str | None = None, ref: str | None = None,
                   active: bool = False, limit: int = Query(20, ge=1, le=200)):
        q = select(Task)
        if job_id is not None:
            q = q.where(Task.job_id == job_id)
        if kind:
            q = q.where(Task.kind == kind)
        if ref:
            q = q.where(Task.ref == ref)
        if active:
            q = q.where(Task.status.in_(sv.ACTIVE))
        return [sv.task_out(t) for t in s.scalars(q.order_by(Task.id.desc()).limit(limit))]

    @r.get("/tasks/{tid}", response_model=TaskOut)
    def get_task(tid: int, s: S):
        return sv.task_out(_get(s, Task, tid, "Task"))

    @r.post("/tasks/{tid}/cancel", response_model=TaskOut)
    def cancel_task(tid: int, request: Request, s: S):
        _get(s, Task, tid, "Task")
        _worker(request).cancel(tid)
        s.expire_all()
        return sv.task_out(_get(s, Task, tid, "Task"))

    # --- admin: backup ---
    @r.get("/admin/backup", response_model=BackupOut)
    def backup_info(request: Request, d: D):
        last = backup_mod.last_backup(d) or {}
        return {"time": last.get("time"), "path": last.get("path"), "size": last.get("size"),
                "folder": request.app.state.backup_dir}

    @r.post("/admin/backup", response_model=BackupOut)
    def backup_now(request: Request, d: D):
        try:
            info = backup_mod.make_backup(d, request.app.state.backup_dir)
        except OSError as e:
            log.exception("backup failed")
            raise sv.UserError(f"Backup failed: {e}", 500, "backup_failed") from e
        log.info("backup written: %s", info["path"])
        return info

    @r.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str):
        raise sv.not_found("Page")

    return r


def _media_type(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return {".nc": "text/plain", ".dxf": "application/dxf", ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".json": "application/json", ".svg": "image/svg+xml",
            ".zip": "application/zip"}.get(ext, "application/octet-stream")


def _first_error(e: Exception) -> str:
    errs = getattr(e, "errors", None)
    if callable(errs):
        try:
            items = errs()
        except Exception:  # noqa: BLE001
            items = []
        if items:
            it = items[0]
            loc = [str(x) for x in it.get("loc", ()) if x not in ("body", "query", "path", "form")]
            msg = str(it.get("msg", "is not valid"))
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            return (f"{'.'.join(loc)}: {msg}" if loc else msg)
    return str(e) or "Bad input"


# ---------- web UI files ----------

WEB_TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
             ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png",
             ".ico": "image/x-icon", ".woff2": "font/woff2"}

def add_web(app: FastAPI, web_dir: str) -> None:
    """Serve web/ at "/": real files as they are, anything else gets
    index.html (the UI uses hash routes, so this is only a safety net)."""
    root = os.path.realpath(web_dir)

    def index() -> Response:
        p = os.path.join(root, "index.html")
        if os.path.isfile(p):
            return FileResponse(p, media_type="text/html")
        return HTMLResponse("<!doctype html><title>Nest</title><p>Nest is running. The web UI files "
                            "(web/index.html) are missing.</p>", status_code=200)

    @app.get("/", include_in_schema=False)
    def home():
        return index()

    @app.get("/{path:path}", include_in_schema=False)
    def web_file(path: str):
        if path.startswith("static/"):
            path = path[len("static/"):]
        full = os.path.realpath(os.path.join(root, path))
        if (full == root or full.startswith(root + os.sep)) and os.path.isfile(full):
            # Fixed types: on Windows the registry can map .js to text/plain,
            # and browsers then refuse to load ES modules.
            return FileResponse(full, media_type=WEB_TYPES.get(os.path.splitext(full)[1].lower()))
        if os.path.splitext(path)[1] and not path.endswith(".html"):
            return JSONResponse({"detail": "File not found"}, status_code=404)
        return index()


# ---------- app factory ----------

def error_json(status: int, message: str, code: str | None = None) -> JSONResponse:
    """Every API error: {detail, code, message, request_id}. `detail` stays for
    older clients; the UI translates `code` (web/i18n.js err_<code>)."""
    code = code or sv.STATUS_CODES.get(status, "server_error" if status >= 500 else "error")
    return JSONResponse({"detail": message, "code": code, "message": message, "request_id": request_id.get()},
                        status_code=status)


def create_app(data_dir: str | None = None, web_dir: str | None = None, backup_dir: str | None = None,
               start_worker: bool = True) -> FastAPI:
    db = get_database(data_dir or default_data_dir())
    setup_logging(db.data_dir)
    with db.session() as s:
        seed(s)
    worker = Worker(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            worker.start()
        log.info("Nest %s started on %s", sv.VERSION, db.data_dir)
        try:
            yield
        finally:
            await run_in_threadpool(worker.stop)

    app = FastAPI(title="Nest", version=sv.VERSION, lifespan=lifespan)
    app.state.db = db
    app.state.worker = worker
    app.state.started_at = time.time()
    app.state.backup_dir = os.path.abspath(backup_dir) if backup_dir else backup_mod.default_backup_dir()

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = (request.headers.get("x-request-id") or "")[:40] or new_request_id()
        token = request_id.set(rid)
        t0 = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001
                log.exception("unhandled error on %s %s", request.method, request.url.path)
                response = error_json(500, "Something went wrong on the server. The details are in the log "
                                           f"(request {rid}).", "server_error")
            response.headers["X-Request-ID"] = rid
            path = request.url.path
            if path.startswith("/api/"):
                ms = (time.perf_counter() - t0) * 1000
                quiet = request.method == "GET" and path.startswith("/api/tasks") and response.status_code < 400
                log.log(logging.DEBUG if quiet else logging.INFO, "%s %s %s %.0f ms", request.method, path,
                        response.status_code, ms)
            return response
        finally:
            request_id.reset(token)

    @app.exception_handler(sv.UserError)
    async def user_error(request: Request, exc: sv.UserError):
        if exc.status >= 500:
            log.error("%s %s: %s", request.method, request.url.path, exc.message)
        resp = error_json(exc.status, exc.message, exc.code)
        if exc.extra:
            import json as _json
            body = _json.loads(resp.body)
            resp = JSONResponse({**exc.extra, **body}, status_code=exc.status)
        return resp

    @app.exception_handler(RequestValidationError)
    async def bad_request(_request: Request, exc: RequestValidationError):
        return error_json(400, _first_error(exc), "bad_input")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        msg = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return error_json(exc.status_code, msg)

    app.include_router(build_router())
    add_web(app, web_dir or WEB_DIR)
    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    # `app` is built on first access (uvicorn "app.main:app" or `from app.main import app`).
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)


__all__ = ["create_app", "app"]
