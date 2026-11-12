"""Background work: nesting and relief runs in a worker inside the app.

- One thread per kind ("nest", "relief"): one task at a time per kind,
  queued tasks run in the order they were made.
- The `tasks` table is the queue and the record (status, progress 0..1,
  message, result JSON), so a closed browser can come back to the result.
- Tasks still "running" when the app starts again were cut off by a stop or
  crash: they become "failed" with the message "Interrupted ...".
- A maintenance thread runs app/maintenance.py at start and every 24 hours.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy import select, update

from . import services as sv
from .db import Database
from .models import Job, ReliefRun, Task, now_utc

log = logging.getLogger("nest.tasks")

KINDS = ("nest", "relief")
INTERRUPTED = "Interrupted: the server stopped while this was running. Run it again."
MAINTENANCE_EVERY_S = 24 * 3600


class TaskContext:
    """Given to a task: report progress, ask whether Cancel was pressed."""

    def __init__(self, worker: "Worker", task_id: int):
        self.worker = worker
        self.task_id = task_id
        self._last = 0.0
        self._last_p = -1.0

    def should_stop(self) -> bool:
        return self.worker.is_cancelled(self.task_id)

    def progress(self, p: float, summary=None) -> None:
        """Engine callback progress(p, summary). Raises TaskCancelled after
        Cancel, so engines that only report progress still stop."""
        if self.should_stop():
            raise sv.TaskCancelled()
        now = time.monotonic()
        try:
            p = max(0.0, min(1.0, float(p)))
        except (TypeError, ValueError):
            return
        if now - self._last < 0.25 and abs(p - self._last_p) < 0.05 and p < 1.0:
            return                               # at most 4 writes a second for small steps
        self._last, self._last_p = now, p
        values = {"progress": p}
        if summary is not None:
            values["message"] = _summary_text(summary)
        self.worker._update(self.task_id, running_only=True, **values)


def _summary_text(summary) -> str:
    if isinstance(summary, str):
        return summary[:500]
    if isinstance(summary, dict):
        parts = []
        for k, v in summary.items():
            if isinstance(v, float):
                v = round(v, 3)
            parts.append(f"{k}: {v}")
        return ", ".join(parts)[:500]
    return str(summary)[:500]


class Worker:
    def __init__(self, db: Database, maintenance: bool = True, poll_s: float = 1.0):
        self.db = db
        self.data_dir = db.data_dir
        self.maintenance = maintenance
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._wake = {k: threading.Event() for k in KINDS}
        self._cancel: set[int] = set()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._running: dict[str, int | None] = {k: None for k in KINDS}
        self._stopping = False
        self.started = False
        self.handlers: dict[str, Callable] = {"nest": _nest_task, "relief": _relief_task}

    # ---- life cycle ----
    def start(self) -> None:
        with self._lock:
            if self.started:
                return
            self.started = True
            self._stop.clear()
            self._stopping = False
        self.recover()
        for k in KINDS:
            th = threading.Thread(target=self._loop, args=(k,), name=f"nest-worker-{k}", daemon=True)
            th.start()
            self._threads.append(th)
        if self.maintenance:
            th = threading.Thread(target=self._maintenance_loop, name="nest-maintenance", daemon=True)
            th.start()
            self._threads.append(th)
        log.info("worker started (%s)", self.data_dir)

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            if not self.started:
                return
            self.started = False
            self._stopping = True
            running = [t for t in self._running.values() if t is not None]
            self._cancel.update(running)          # ask running work to stop; recorded as interrupted
        self._stop.set()
        for ev in self._wake.values():
            ev.set()
        end = time.monotonic() + timeout
        for th in self._threads:
            th.join(max(0.0, end - time.monotonic()))
        self._threads = []
        log.info("worker stopped")

    def recover(self) -> int:
        """Tasks left "running" by a stop or crash become failed: interrupted."""
        with self.db.session() as s:
            res = s.execute(update(Task).where(Task.status == "running")
                            .values(status="failed", message=INTERRUPTED, finished_at=now_utc()))
            s.commit()
            n = res.rowcount or 0
        if n:
            log.warning("%d task(s) were interrupted by the last stop", n)
        return n

    # ---- queue ----
    def submit(self, kind: str, params: dict, job_id: int | None = None, ref: str | None = None) -> Task:
        if kind not in KINDS:
            raise ValueError(kind)
        with self.db.session() as s:
            t = Task(kind=kind, job_id=job_id, ref=ref, status="queued", progress=0.0,
                     message="Waiting in the queue", params_json=json.dumps(params))
            s.add(t)
            s.commit()
            s.refresh(t)
        log.info("task %s queued: %s job=%s ref=%s", t.id, kind, job_id, ref)
        self.start()
        self._wake[kind].set()
        return t

    def cancel(self, task_id: int) -> None:
        with self.db.session() as s:
            t = s.get(Task, task_id)
            if t is None:
                raise sv.not_found("Task")
            if t.status == "queued":
                s.execute(update(Task).where(Task.id == task_id, Task.status == "queued")
                          .values(status="cancelled", message="Cancelled", finished_at=now_utc()))
                s.commit()
                s.refresh(t)
            if t.status == "running":
                with self._lock:
                    self._cancel.add(task_id)
                t.message = "Cancelling…"
                s.commit()
        log.info("task %s: cancel asked", task_id)

    def is_cancelled(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancel

    def wait(self, task_id: int, timeout: float = 60.0) -> Task:
        """Block until the task is finished (tests and scripts)."""
        end = time.monotonic() + timeout
        while True:
            with self.db.session() as s:
                t = s.get(Task, task_id)
                if t is None or t.status not in sv.ACTIVE or time.monotonic() > end:
                    return t
            time.sleep(0.05)

    # ---- running ----
    def _loop(self, kind: str) -> None:
        while not self._stop.is_set():
            try:
                tid = self._claim(kind)
            except Exception:  # noqa: BLE001  (database busy: try again)
                log.exception("could not read the task queue")
                tid = None
            if tid is None:
                self._wake[kind].wait(self.poll_s)
                self._wake[kind].clear()
                continue
            self._running[kind] = tid
            try:
                self._execute(tid)
            finally:
                self._running[kind] = None
                with self._lock:
                    self._cancel.discard(tid)

    def _claim(self, kind: str) -> int | None:
        with self.db.session() as s:
            t = s.scalars(select(Task).where(Task.kind == kind, Task.status == "queued")
                          .order_by(Task.id)).first()
            if t is None:
                return None
            res = s.execute(update(Task).where(Task.id == t.id, Task.status == "queued")
                            .values(status="running", started_at=now_utc(), message="Running", progress=0.0))
            s.commit()
            return t.id if res.rowcount else None

    def _update(self, task_id: int, running_only: bool = False, **values) -> None:
        try:
            with self.db.session() as s:
                q = update(Task).where(Task.id == task_id)
                if running_only:
                    q = q.where(Task.status == "running")
                s.execute(q.values(**values))
                s.commit()
        except Exception:  # noqa: BLE001  (progress is best effort)
            log.exception("could not save task %s progress", task_id)

    def _execute(self, task_id: int) -> None:
        with self.db.session() as s:
            t = s.get(Task, task_id)
            kind, job_id, ref, params = t.kind, t.job_id, t.ref, json.loads(t.params_json or "{}")
        ctx = TaskContext(self, task_id)
        t0 = time.perf_counter()
        log.info("task %s started: %s job=%s ref=%s", task_id, kind, job_id, ref)
        try:
            result = self.handlers[kind](self, ctx, job_id, ref, params)
        except sv.TaskCancelled:
            if self._stopping:
                self._update(task_id, status="failed", message=INTERRUPTED, finished_at=now_utc())
            else:
                self._update(task_id, status="cancelled", message="Cancelled", finished_at=now_utc())
            log.info("task %s cancelled", task_id)
            return
        except sv.UserError as e:
            self._update(task_id, status="failed", message=e.message, finished_at=now_utc(),
                         result_json=json.dumps({"code": e.code, "status": e.status}))
            log.info("task %s failed: %s", task_id, e.message)
            return
        except Exception as e:  # noqa: BLE001
            log.exception("task %s crashed", task_id)
            self._update(task_id, status="failed", finished_at=now_utc(),
                         message=f"Unexpected error ({type(e).__name__}). Details are in the log.",
                         result_json=json.dumps({"code": "server_error", "status": 500}))
            return
        cancelled = self.is_cancelled(task_id)
        self._update(task_id, status="cancelled" if cancelled else "done", progress=1.0,
                     message="Cancelled" if cancelled else "Done", finished_at=now_utc(),
                     result_json=json.dumps(result))
        log.info("task %s %s in %.1f s", task_id, "cancelled" if cancelled else "done", time.perf_counter() - t0)

    def _maintenance_loop(self) -> None:
        from . import maintenance
        while not self._stop.is_set():
            try:
                maintenance.run_all(self.db)
            except Exception:  # noqa: BLE001
                log.exception("maintenance failed")
            self._stop.wait(MAINTENANCE_EVERY_S)


# ---------- task bodies ----------

def _nest_task(worker: Worker, ctx: TaskContext, job_id, _ref, params: dict) -> dict:
    from .schemas import NestIn
    with worker.db.session() as s:
        job = s.get(Job, job_id)
        if job is None or job.deleted_at is not None:
            raise sv.not_found("Job")
        ctx.progress(0.0, "Nesting")
        out = sv.run_nest(s, worker.data_dir, job, NestIn(**params), progress=ctx.progress,
                          should_stop=ctx.should_stop)
    return dict(job_id=job_id, sheets_used=out["sheets_used"], utilisation=out["utilisation"],
                unplaced=sum(int(u["count"]) for u in out["unplaced"]), elapsed_s=out["elapsed_s"])


def _relief_task(worker: Worker, ctx: TaskContext, _job_id, ref, params: dict) -> dict:
    import shutil
    create = bool(params.get("create"))
    with worker.db.session() as s:
        run = s.get(ReliefRun, ref)
        if run is None:
            raise sv.not_found("Relief")
        ctx.progress(0.05, "Making the relief")
        if create:
            p = json.loads(run.params_json)
            try:
                res = sv._run_relief(run, p, worker.data_dir)
            except Exception:
                s.delete(run)
                s.commit()
                shutil.rmtree(sv.relief_dir(worker.data_dir, ref), ignore_errors=True)
                raise
            if ctx.should_stop():               # nothing to keep: the run goes away
                s.delete(run)
                s.commit()
                shutil.rmtree(sv.relief_dir(worker.data_dir, ref), ignore_errors=True)
                raise sv.TaskCancelled()
            run.result_json = json.dumps(res)
            s.commit()
        else:
            # the old files are replaced by the new ones, so a late Cancel keeps the new result
            sv.rerun_relief(s, worker.data_dir, run, params.get("changes") or {})
    return {"relief_id": ref}
