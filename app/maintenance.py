"""Nightly clean-up (run by the worker at start and every 24 hours):

- export files (G-code, DXF) older than 90 days are deleted, except for
  jobs marked as cut: those are the proof of what was cut;
- relief runs whose files are all older than 90 days are deleted;
- jobs in the bin for more than 30 days are deleted for good;
- finished task records older than 30 days are dropped.
Offcuts are never touched.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import timedelta

from sqlalchemy import delete, select

from . import services as sv
from .db import Database
from .models import Job, ReliefRun, Task, now_utc

log = logging.getLogger("nest.maintenance")

FILE_DAYS = 90
TASK_DAYS = 30


def _old(path: str, cutoff: float) -> bool:
    try:
        return os.path.getmtime(path) < cutoff
    except OSError:
        return False


def clean_old_files(db: Database, days: int = FILE_DAYS, now: float | None = None) -> dict:
    cutoff = (now or time.time()) - days * 86400
    removed_exports, removed_relief = 0, 0
    with db.session() as s:
        for job in s.scalars(select(Job).where(Job.status != "cut")):
            folder = sv.job_dir(db.data_dir, job.id)
            if not os.path.isdir(folder):
                continue
            gone = set()
            for rec in sv.exports_of(job):
                name = rec.get("name", "")
                sub = rec.get("dir") or ""
                path = os.path.join(folder, sub, name)
                if os.path.isfile(path) and _old(path, cutoff):
                    os.remove(path)
                    gone.add(name)
                    d = os.path.dirname(path)
                    if sub and os.path.isdir(d) and not os.listdir(d):
                        os.rmdir(d)
            if gone:
                removed_exports += len(gone)
                recs = [r for r in sv.exports_of(job) if r.get("name") not in gone]
                job.exports_json = json.dumps(recs)
                if job.status == "exported" and not recs:
                    job.status = "nested"
        busy = {t.ref for t in s.scalars(select(Task).where(Task.kind == "relief", Task.status.in_(sv.ACTIVE)))}
        root = os.path.join(db.data_dir, "relief")
        known = set()
        for run in s.scalars(select(ReliefRun)):
            known.add(run.id)
            if run.id in busy:
                continue
            folder = sv.relief_dir(db.data_dir, run.id)
            files = [os.path.join(folder, n) for n in os.listdir(folder)] if os.path.isdir(folder) else []
            if all(_old(f, cutoff) for f in files) and (files or _created_before(run.created_at, days)):
                shutil.rmtree(folder, ignore_errors=True)
                s.delete(run)
                removed_relief += 1
        if os.path.isdir(root):                  # folders of failed runs that have no row
            for name in os.listdir(root):
                folder = os.path.join(root, name)
                if name not in known and name not in busy and os.path.isdir(folder) and _old(folder, cutoff):
                    shutil.rmtree(folder, ignore_errors=True)
        s.commit()
    return {"export_files": removed_exports, "relief_runs": removed_relief}


def _created_before(dt, days: int) -> bool:
    return dt is not None and dt < now_utc() - timedelta(days=days)


def purge_bin(db: Database, days: int = sv.BIN_DAYS) -> int:
    cutoff = now_utc() - timedelta(days=days)
    n = 0
    with db.session() as s:
        for job in list(s.scalars(select(Job).where(Job.deleted_at.is_not(None), Job.deleted_at < cutoff))):
            sv.purge_job(s, db.data_dir, job)
            n += 1
    return n


def prune_tasks(db: Database, days: int = TASK_DAYS) -> int:
    cutoff = now_utc() - timedelta(days=days)
    with db.session() as s:
        res = s.execute(delete(Task).where(Task.status.not_in(sv.ACTIVE), Task.created_at < cutoff))
        s.commit()
        return res.rowcount or 0


def run_all(db: Database) -> dict:
    out = clean_old_files(db)
    out["jobs_purged"] = purge_bin(db)
    out["tasks_pruned"] = prune_tasks(db)
    if any(out.values()):
        log.info("clean-up: %s", out)
    return out
