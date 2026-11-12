"""FastAPI app: the /api routes of PLAN.md section 4.7 plus the web UI files.

create_app(data_dir) builds a fresh app on one data folder (tests use a
temporary folder). `app` (module level) is made on first use from
NEST_DATA_DIR, so importing this module does not touch ./data.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services as sv
from .db import Database, default_data_dir, get_database, session_scope
from .models import FeedOverride, Job, JobPart, Machine, Material, Offcut, ReliefRun, Tool
from .schemas import (ChecksOut, ChecksQuery, CommitOut, ExportIn, ExportOut, FeedIn, FeedOut, FeedUpdate,
                      JobCreate, JobDetail, JobListItem, JobUpdate,
                      MachineIn, MachineOut, MachineUpdate, MaterialIn, MaterialOut, MaterialUpdate, NestIn,
                      NestOut, OffcutOut, PartOut, PartUpdate, ReliefIn, ReliefOut, ToolIn, ToolOut, ToolUpdate,
                      UploadOut)
from .seed import seed

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


# ---------- routes ----------

def build_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health")
    def health():
        return {"ok": True, "version": sv.VERSION}

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
    def list_jobs(s: S):
        mats = {m.id: m for m in s.scalars(select(Material))}
        machs = {m.id: m for m in s.scalars(select(Machine))}
        jobs = s.scalars(select(Job).order_by(Job.created_at.desc(), Job.id.desc()))
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
        return sv.job_detail(s, _get(s, Job, jid, "Job"))

    @r.patch("/jobs/{jid}", response_model=JobDetail)
    def update_job(jid: int, body: JobUpdate, s: S):
        j = _get(s, Job, jid, "Job")
        changes = body.model_dump(exclude_unset=True)
        if changes.get("name") is None:
            changes.pop("name", None)
        _check_links(s, changes.get("material_id"), changes.get("machine_id"))
        _apply(j, changes)
        s.commit()
        return sv.job_detail(s, j)

    @r.delete("/jobs/{jid}", status_code=204)
    def delete_job(jid: int, s: S, d: D):
        sv.delete_job(s, d, _get(s, Job, jid, "Job"))
        return Response(status_code=204)

    @r.post("/jobs/{jid}/files", response_model=UploadOut)
    def upload_files(jid: int, s: S, file: list[UploadFile] = File(...)):
        return sv.import_files(s, _get(s, Job, jid, "Job"), file)

    def _part_row(s: Session, jid: int, pid: str) -> JobPart:
        _get(s, Job, jid, "Job")
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

    @r.post("/jobs/{jid}/nest", response_model=NestOut)
    def nest_job(jid: int, s: S, d: D, body: NestIn | None = None):
        return sv.run_nest(s, d, _get(s, Job, jid, "Job"), body or NestIn())

    @r.post("/jobs/{jid}/export", response_model=ExportOut)
    def export_job(jid: int, body: ExportIn, s: S, d: D):
        return sv.run_export(s, d, _get(s, Job, jid, "Job"), body)

    @r.get("/jobs/{jid}/checks", response_model=ChecksOut)
    def job_checks(jid: int, s: S, q: Annotated[ChecksQuery, Query()]):
        return sv.run_checks(s, _get(s, Job, jid, "Job"), q)

    @r.get("/jobs/{jid}/export.zip")
    def export_zip(jid: int, s: S, d: D, all: bool = False):
        j = _get(s, Job, jid, "Job")
        data = sv.export_zip(d, j, all_revisions=all)
        name = sv.slug(j.name, f"job{j.id}") + ".zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @r.get("/jobs/{jid}/files/{name}")
    def job_file(jid: int, name: str, s: S, d: D):
        path = sv.job_file_path(d, _get(s, Job, jid, "Job"), name)
        return FileResponse(path, filename=name, media_type=_media_type(name))

    @r.post("/jobs/{jid}/commit", response_model=CommitOut)
    def commit_job(jid: int, s: S):
        j = _get(s, Job, jid, "Job")
        saved, used = sv.commit_job(s, j)
        return {**sv.job_detail(s, j), "offcuts_saved": [sv.offcut_out(o) for o in saved],
                "offcuts_used": used}

    # --- relief ---
    @r.post("/relief", response_model=ReliefOut)
    def create_relief(
        s: S, d: D,
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
        return sv.create_relief(s, d, image, params)

    @r.get("/relief/{rid}", response_model=ReliefOut)
    def get_relief(rid: str, s: S):
        return sv.relief_out(_get(s, ReliefRun, rid, "Relief"))

    @r.post("/relief/{rid}", response_model=ReliefOut)
    def rerun_relief(rid: str, body: ReliefIn, s: S, d: D):
        run = _get(s, ReliefRun, rid, "Relief")
        return sv.rerun_relief(s, d, run, body.model_dump(exclude_unset=True, exclude_none=True))

    @r.get("/relief/{rid}/files/{name}")
    def relief_file(rid: str, name: str, s: S, d: D):
        if not sv.is_safe_name(rid):
            raise sv.UserError("Bad id")
        _get(s, ReliefRun, rid, "Relief")
        path = sv.safe_file(sv.relief_dir(d, rid), name)
        return FileResponse(path, filename=name, media_type=_media_type(name))

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

def create_app(data_dir: str | None = None, web_dir: str | None = None) -> FastAPI:
    db = get_database(data_dir or default_data_dir())
    with db.session() as s:
        seed(s)

    app = FastAPI(title="Nest", version=sv.VERSION)
    app.state.db = db

    @app.exception_handler(sv.UserError)
    async def user_error(_request: Request, exc: sv.UserError):
        return JSONResponse({**exc.extra, "detail": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def bad_request(_request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": _first_error(exc)}, status_code=400)

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
