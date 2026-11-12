"""Platform tests (PLAN.md Part 2, items 3.1, 3.2, 3.4, 3.5, 3.6): background
tasks, migrations, backup/restore, logging/health/errors, clean-up, duplicate,
bin and lock. Run: python -m pytest -q tests/test_platform.py"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import zipfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app import backup as backup_mod
from app import maintenance
from app import services as sv
from app.db import get_database
from app.main import create_app
from app.models import Job, Task, now_utc

from test_api import FAST, SIGN_PNG, TINY_SVG, by_name, new_job, upload


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "data"), backup_dir=str(tmp_path / "backups"))
    with TestClient(app) as c:
        c.data_dir = str(tmp_path / "data")
        c.backup_dir = str(tmp_path / "backups")
        c.worker = app.state.worker
        c.db = app.state.db
        yield c


def job_with_parts(c, name="Job", machine="Router (NC Studio)"):
    j = new_job(c, name, machine=machine)
    upload(c, j["id"], "tiny.svg", TINY_SVG)
    return j


def wait_task(c, tid, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        t = c.get(f"/api/tasks/{tid}").json()
        if t["status"] not in ("queued", "running"):
            return t
        time.sleep(0.05)
    raise AssertionError(f"task {tid} did not finish: {t}")


def wait_for(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class SlowEngine:
    """Wraps the real engine: reports progress, then waits for `go` or Cancel."""

    def __init__(self):
        from nesting import engine
        self.real = engine.nest
        self.go = threading.Event()
        self.started = threading.Event()

    def __call__(self, parts, sheets, settings, progress=None, should_stop=None):
        self.started.set()
        if progress:
            progress(0.5, {"placed": 3, "sheets": 1})
        while not self.go.wait(0.02):
            if should_stop and should_stop():
                break
        return self.real(parts, sheets, settings)


# ---------- 3.2 migrations ----------

def test_migrated_schema_matches_models(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from app.db import Base
    from app.migrate import current_revisions, head_revisions

    db = get_database(str(tmp_path / "m"))
    assert current_revisions(db.engine) == head_revisions()
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
    assert diff == [], f"models and migrations differ (add a migration): {diff}"


def test_old_database_is_stamped_and_upgraded(tmp_path):
    """A v1 database (tables, no alembic_version) keeps its jobs."""
    from alembic import command

    from app.migrate import alembic_config, current_revisions, head_revisions
    d = tmp_path / "old"
    d.mkdir()
    path = str(d / "nest.db")
    eng = create_engine(f"sqlite:///{path}")
    cfg = alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE alembic_version")
    raw.execute("INSERT INTO jobs (name, status, exports_json, created_at) VALUES ('Old job', 'draft', '[]', "
                "'2026-01-02 03:04:05')")
    raw.commit()
    raw.close()
    eng.dispose()

    app = create_app(str(d), start_worker=False)
    db = app.state.db
    assert current_revisions(db.engine) == head_revisions()
    cols = {c["name"] for c in inspect(db.engine).get_columns("jobs")}
    assert {"deleted_at", "settings_json"} <= cols
    c = TestClient(app)
    rows = c.get("/api/jobs").json()
    assert [r["name"] for r in rows] == ["Old job"]


# ---------- 3.1 background tasks ----------

def test_call_nest_adapter_passes_only_known_kwargs():
    seen = {}

    def plain(a, b, c):
        seen["plain"] = True

    def prog(a, b, c, progress=None):
        seen["prog"] = progress

    def both(a, b, c, *, progress=None, should_stop=None):
        seen["both"] = (progress, should_stop)

    def anykw(a, b, c, **kw):
        seen["any"] = set(kw)

    p, st = (lambda *_: None), (lambda: False)
    for fn in (plain, prog, both, anykw):
        sv.call_nest(fn, 1, 2, 3, progress=p, should_stop=st)
    assert seen["plain"] and seen["prog"] is p and seen["both"] == (p, st) and seen["any"] == {"progress",
                                                                                                "should_stop"}
    sv.call_nest(both, 1, 2, 3)
    assert seen["both"] == (None, None)


def test_async_nest_gives_task_and_result(client):
    j = job_with_parts(client)
    r = client.post(f"/api/jobs/{j['id']}/nest?async=1", json=FAST)
    assert r.status_code == 202, r.text
    t = r.json()
    assert t["task_id"] and t["kind"] == "nest" and t["job_id"] == j["id"] and t["status"] in ("queued", "running")
    done = wait_task(client, t["task_id"])
    assert done["status"] == "done" and done["progress"] == 1.0, done
    assert done["result"]["sheets_used"] >= 1 and done["finished_at"]
    job = client.get(f"/api/jobs/{j['id']}").json()
    assert job["status"] == "nested" and job["nest"]["sheets_used"] == done["result"]["sheets_used"]
    listed = client.get(f"/api/tasks?job_id={j['id']}&kind=nest").json()
    assert [x["id"] for x in listed] == [t["task_id"]]
    assert client.get(f"/api/tasks?job_id={j['id']}&active=1").json() == []
    # bad input fails at once, not in the background
    j2 = new_job(client, "Empty")
    r = client.post(f"/api/jobs/{j2['id']}/nest?async=1", json=FAST)
    assert r.status_code == 400 and r.json()["code"] == "bad_input"
    assert client.get("/api/tasks/9999").status_code == 404


def test_one_task_at_a_time_cancel_and_progress(client, monkeypatch):
    from nesting import engine
    slow = SlowEngine()
    monkeypatch.setattr(engine, "nest", slow)
    a, b = job_with_parts(client, "A"), job_with_parts(client, "B")
    ta = client.post(f"/api/jobs/{a['id']}/nest?async=1", json=FAST).json()["task_id"]
    tb = client.post(f"/api/jobs/{b['id']}/nest?async=1", json=FAST).json()["task_id"]
    assert slow.started.wait(10)
    assert wait_for(lambda: client.get(f"/api/tasks/{ta}").json()["progress"] == 0.5)
    run = client.get(f"/api/tasks/{ta}").json()
    assert run["status"] == "running" and "placed: 3" in run["message"]
    assert client.get(f"/api/tasks/{tb}").json()["status"] == "queued"      # waits its turn
    # a second nest of the same job, an export or commit while nesting: refused
    r = client.post(f"/api/jobs/{a['id']}/nest?async=1", json=FAST)
    assert r.status_code == 409 and r.json()["code"] == "task_busy"
    assert client.post(f"/api/jobs/{a['id']}/export", json={"target": "ruida"}).json()["code"] == "task_busy"
    # cancel the queued one, then the running one
    assert client.post(f"/api/tasks/{tb}/cancel").json()["status"] == "cancelled"
    client.post(f"/api/tasks/{ta}/cancel")
    assert wait_task(client, ta)["status"] == "cancelled"
    assert client.get(f"/api/tasks/{tb}").json()["status"] == "cancelled"
    for j in (a, b):
        job = client.get(f"/api/jobs/{j['id']}").json()
        assert job["nest"] is None and job["status"] == "draft"       # a cancelled nest saves nothing
    # the queue keeps going: a new task runs to the end
    slow.go.set()
    tc = client.post(f"/api/jobs/{b['id']}/nest?async=1", json=FAST).json()["task_id"]
    assert wait_task(client, tc)["status"] == "done"
    assert client.get(f"/api/jobs/{b['id']}").json()["status"] == "nested"


def test_running_tasks_become_interrupted_after_restart(tmp_path):
    d = str(tmp_path / "data")
    create_app(d, start_worker=False)
    db = get_database(d)
    with db.session() as s:
        s.add(Task(kind="nest", job_id=1, status="running", progress=0.4, message="Running", params_json="{}"))
        s.add(Task(kind="nest", job_id=99, status="queued", progress=0, message="", params_json="{}"))
        s.commit()
    with TestClient(create_app(d)) as c:
        tasks = {t["job_id"]: t for t in c.get("/api/tasks").json()}
        assert tasks[1]["status"] == "failed" and tasks[1]["message"].startswith("Interrupted")
        # the queued one still runs (and fails cleanly: that job does not exist)
        t = wait_task(c, tasks[99]["id"])
        assert t["status"] == "failed" and "not found" in t["message"]


@pytest.mark.skipif(not os.path.isfile(SIGN_PNG), reason="samples/sign.png missing")
def test_async_relief(client):
    with open(SIGN_PNG, "rb") as fh:
        img = fh.read()
    r = client.post("/api/relief?async=1", files={"image": ("sign.png", img, "image/png")},
                    data={"width_mm": "120", "material_mm": "18", "resolution_mm": "1"})
    assert r.status_code == 202, r.text
    t = r.json()
    rid = t["ref"]
    assert t["kind"] == "relief" and rid
    done = wait_task(client, t["task_id"], 120)
    assert done["status"] == "done" and done["result"] == {"relief_id": rid}
    out = client.get(f"/api/relief/{rid}").json()
    assert out["id"] == rid and len(out["files"]) == 2
    styles = [dict(rg) for rg in out["regions"]]
    r = client.post(f"/api/relief/{rid}?async=1", json={"styles": styles, "max_height_mm": 4})
    assert r.status_code == 202
    assert wait_task(client, r.json()["task_id"], 120)["status"] == "done"
    assert client.get(f"/api/relief/{rid}").json()["params"]["max_height_mm"] == 4
    # a bad picture fails in the background and leaves nothing behind
    r = client.post("/api/relief?async=1", files={"image": ("x.png", b"not a picture", "image/png")},
                    data={"width_mm": "100", "material_mm": "18"})
    bad = wait_task(client, r.json()["task_id"])
    assert bad["status"] == "failed" and "picture" in bad["message"]
    assert client.get(f"/api/relief/{bad['ref']}").status_code == 404
    assert not os.path.isdir(sv.relief_dir(client.data_dir, bad["ref"]))


# ---------- 3.4 backup ----------

def _flow(c):
    j = job_with_parts(c, "Backup me")
    assert c.post(f"/api/jobs/{j['id']}/nest", json=FAST).status_code == 200
    r = c.post(f"/api/jobs/{j['id']}/export", json={"target": "ncstudio"})
    assert r.status_code == 200, r.text
    return c.get(f"/api/jobs/{j['id']}").json()


def test_backup_then_restore_gives_identical_jobs(client, tmp_path):
    job = _flow(client)
    assert client.get("/api/admin/backup").json()["time"] is None
    r = client.post("/api/admin/backup")
    assert r.status_code == 200, r.text
    info = r.json()
    assert os.path.isfile(info["path"]) and info["folder"] == client.backup_dir
    assert client.get("/api/admin/backup").json()["time"] == info["time"]
    assert client.get("/api/health").json()["last_backup"] == info["time"]
    with zipfile.ZipFile(info["path"]) as z:
        names = set(z.namelist())
    assert "nest.db" in names and not any(n.startswith("logs/") for n in names)
    assert all(f"jobs/{job['id']}/{f['name']}" in names for f in job["exports"])

    dest = str(tmp_path / "restored")
    backup_mod.restore(info["path"], dest)
    with pytest.raises(FileExistsError):
        backup_mod.restore(info["path"], dest)                   # never over existing data
    c2 = TestClient(create_app(dest, start_worker=False))
    assert c2.get(f"/api/jobs/{job['id']}").json() == job
    assert c2.get("/api/jobs").json() == client.get("/api/jobs").json()
    for f in job["exports"]:
        assert c2.get(f["url"]).content == client.get(f["url"]).content


def test_backup_keeps_newest_14_and_cli(client, tmp_path, capsys):
    target = str(tmp_path / "many")
    for _ in range(16):
        backup_mod.make_backup(client.data_dir, target)
    names = backup_mod.list_backups(target)
    assert len(names) == 14
    assert backup_mod.main(["--data", client.data_dir, "--to", target]) == 0
    assert "Backup written" in capsys.readouterr().out
    assert len(backup_mod.list_backups(target)) == 14
    newest = os.path.join(target, backup_mod.list_backups(target)[-1])
    assert backup_mod.main(["--restore", newest, "--to", str(tmp_path / "r2")]) == 0
    assert os.path.isfile(tmp_path / "r2" / "nest.db")
    assert backup_mod.main(["--restore", newest, "--to", str(tmp_path / "r2")]) == 1


def test_default_backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NEST_BACKUP_DIR", str(tmp_path / "x"))
    assert backup_mod.default_backup_dir() == str(tmp_path / "x")
    monkeypatch.delenv("NEST_BACKUP_DIR")
    expect = r"D:\Site backups\nest" if os.name == "nt" else os.path.abspath("./backups")
    assert backup_mod.default_backup_dir() == expect


# ---------- 3.5 logging, health, errors ----------

def test_request_id_log_file_and_error_format(client, monkeypatch):
    r = client.get("/api/jobs", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
    assert client.get("/api/jobs").headers["X-Request-ID"]
    e = client.get("/api/jobs/9999").json()
    assert e["code"] == "not_found" and e["message"] == e["detail"] == "Job not found" and e["request_id"]
    e = client.post("/api/jobs", json={"name": ""}).json()
    assert e["code"] == "bad_input"
    e = client.get("/api/jobs/abc")
    assert e.status_code == 400 and e.json()["code"] == "bad_input"

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")
    j = new_job(client)
    monkeypatch.setattr(sv, "job_detail", boom)
    r = client.get(f"/api/jobs/{j['id']}", headers={"X-Request-ID": "req-500"})
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "server_error" and "req-500" in body["message"] and "kaboom" not in body["message"]
    for h in __import__("logging").getLogger("nest").handlers:
        h.flush()
    text = open(os.path.join(client.data_dir, "logs", "nest.log"), encoding="utf-8").read()
    assert "[abc123]" in text and "GET /api/jobs 200" in text
    assert "[req-500]" in text and "Traceback" in text and "RuntimeError: kaboom" in text


def test_log_rotation_settings(client):
    from logging.handlers import RotatingFileHandler

    from app import logs
    hs = [h for h in logs.log.handlers if isinstance(h, RotatingFileHandler)]
    assert len(hs) == 1 and hs[0].maxBytes == 5 * 1024 * 1024 and hs[0].backupCount + 1 == 5


# ---------- 3.5 clean-up ----------

def _age(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_cleanup_old_files_but_not_cut_jobs(client):
    old = _flow(client)
    cut = _flow(client)
    assert client.post(f"/api/jobs/{cut['id']}/commit").status_code == 200
    for j in (old, cut):
        for f in j["exports"]:
            _age(os.path.join(sv.job_dir(client.data_dir, j["id"]), f["name"]), 100)
    out = maintenance.clean_old_files(client.db)
    assert out["export_files"] == len(old["exports"])
    o = client.get(f"/api/jobs/{old['id']}").json()
    assert o["exports"] == [] and o["status"] == "nested" and o["nest"] is not None
    k = client.get(f"/api/jobs/{cut['id']}").json()
    assert k["exports"] == cut["exports"]
    for f in cut["exports"]:
        assert client.get(f["url"]).status_code == 200
    assert client.get("/api/offcuts").json()                     # offcuts untouched
    # fresh files stay
    fresh = _flow(client)
    maintenance.clean_old_files(client.db)
    assert client.get(f"/api/jobs/{fresh['id']}").json()["exports"] == fresh["exports"]


@pytest.mark.skipif(not os.path.isfile(SIGN_PNG), reason="samples/sign.png missing")
def test_cleanup_old_relief(client):
    with open(SIGN_PNG, "rb") as fh:
        r = client.post("/api/relief", files={"image": ("sign.png", fh.read(), "image/png")},
                        data={"width_mm": "80", "material_mm": "18", "resolution_mm": "1"})
    rid = r.json()["id"]
    folder = sv.relief_dir(client.data_dir, rid)
    maintenance.clean_old_files(client.db)
    assert client.get(f"/api/relief/{rid}").status_code == 200
    for n in os.listdir(folder):
        _age(os.path.join(folder, n), 91)
    assert maintenance.clean_old_files(client.db)["relief_runs"] == 1
    assert client.get(f"/api/relief/{rid}").status_code == 404 and not os.path.isdir(folder)


# ---------- 3.6 duplicate, bin, lock ----------

def test_duplicate_copies_parts_and_settings_not_results(client):
    job = _flow(client)
    client.patch(f"/api/jobs/{job['id']}/parts/{job['parts'][0]['id']}", json={"qty": 4})
    r = client.post(f"/api/jobs/{job['id']}/duplicate")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["id"] != job["id"] and d["name"] == "Backup me (copy)" and d["status"] == "draft"
    assert d["material_id"] == job["material_id"] and d["machine_id"] == job["machine_id"]
    assert d["nest"] is None and d["exports"] == [] and d["settings_saved"] is True
    assert d["settings"] == job["settings"]
    assert [(p["id"], p["name"], p["width"]) for p in d["parts"]] == [(p["id"], p["name"], p["width"])
                                                                         for p in job["parts"]]
    assert d["parts"][0]["qty"] == 4
    # the copy is independent
    client.patch(f"/api/jobs/{d['id']}/parts/{d['parts'][0]['id']}", json={"qty": 9})
    assert client.get(f"/api/jobs/{job['id']}").json()["parts"][0]["qty"] == 4
    assert client.post(f"/api/jobs/{d['id']}/nest", json=FAST).status_code == 200


def test_bin_restore_and_purge(client):
    job = _flow(client)
    jid = job["id"]
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert client.get(f"/api/jobs/{jid}").status_code == 404
    assert jid not in [j["id"] for j in client.get("/api/jobs").json()]
    binned = client.get("/api/jobs?bin=1").json()
    assert [j["id"] for j in binned] == [jid] and binned[0]["deleted_at"] and binned[0]["purge_at"]
    assert client.delete(f"/api/jobs/{jid}").status_code == 404          # already in the bin
    assert client.post(f"/api/jobs/{jid}/nest", json=FAST).status_code == 404
    assert os.path.isdir(sv.job_dir(client.data_dir, jid))              # files kept while in the bin
    r = client.post(f"/api/jobs/{jid}/restore")
    assert r.status_code == 200 and r.json()["deleted_at"] is None
    assert client.get(f"/api/jobs/{jid}").json()["exports"] == job["exports"]
    assert client.delete(f"/api/jobs/{jid}?purge=1").status_code == 409  # not in the bin
    client.delete(f"/api/jobs/{jid}")
    assert client.delete(f"/api/jobs/{jid}?purge=1").status_code == 204
    assert client.get("/api/jobs?bin=1").json() == []
    assert not os.path.isdir(sv.job_dir(client.data_dir, jid))
    # automatic purge after 30 days
    a, b = new_job(client, "Old bin"), new_job(client, "New bin")
    client.delete(f"/api/jobs/{a['id']}")
    client.delete(f"/api/jobs/{b['id']}")
    with client.db.session() as s:
        s.get(Job, a["id"]).deleted_at = now_utc() - timedelta(days=31)
        s.commit()
    assert maintenance.purge_bin(client.db) == 1
    assert [j["name"] for j in client.get("/api/jobs?bin=1").json()] == ["New bin"]


def test_cut_job_is_locked_until_unlocked(client):
    job = _flow(client)
    jid, pid = job["id"], job["parts"][0]["id"]
    c = client.post(f"/api/jobs/{jid}/commit").json()
    assert c["locked"] is True and len(c["offcuts_saved"]) == 1
    laser = by_name(client.get("/api/machines").json(), "Laser (Ruida)")
    refused = [
        client.post(f"/api/jobs/{jid}/files", files=[("file", ("t.svg", TINY_SVG, "image/svg+xml"))]),
        client.patch(f"/api/jobs/{jid}/parts/{pid}", json={"qty": 2}),
        client.delete(f"/api/jobs/{jid}/parts/{pid}"),
        client.post(f"/api/jobs/{jid}/nest", json=FAST),
        client.post(f"/api/jobs/{jid}/nest?async=1", json=FAST),
        client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio"}),
        client.patch(f"/api/jobs/{jid}", json={"machine_id": laser["id"]}),
    ]
    for r in refused:
        assert r.status_code == 409 and r.json()["code"] == "job_locked", r.text
    assert client.patch(f"/api/jobs/{jid}", json={"name": "Renamed"}).status_code == 200
    assert client.get(f"/api/jobs/{jid}/export.zip").status_code == 200       # downloads still work
    r = client.post(f"/api/jobs/{jid}/unlock")
    assert r.status_code == 200 and r.json()["status"] == "exported" and r.json()["locked"] is False
    assert client.get("/api/offcuts").json() == []                # its offcut left the library
    assert client.post(f"/api/jobs/{jid}/unlock").status_code == 409
    assert client.patch(f"/api/jobs/{jid}/parts/{pid}", json={"qty": 2}).status_code == 200
    assert client.post(f"/api/jobs/{jid}/nest", json=FAST).status_code == 200
    assert client.post(f"/api/jobs/{jid}/commit").status_code == 200
    assert len(client.get("/api/offcuts").json()) == 1            # counted once


def test_unlock_returns_used_offcuts(client):
    first = _flow(client)
    oc = client.post(f"/api/jobs/{first['id']}/commit").json()["offcuts_saved"][0]
    j2 = job_with_parts(client, "Reuse")
    client.post(f"/api/jobs/{j2['id']}/nest", json={**FAST, "use_offcuts": True})
    assert client.post(f"/api/jobs/{j2['id']}/commit").json()["offcuts_used"] == [oc["id"]]
    client.post(f"/api/jobs/{j2['id']}/unlock")
    assert {o["id"]: o["used"] for o in client.get("/api/offcuts").json()}[oc["id"]] is False


def test_i18n_dictionaries_have_the_same_keys():
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "i18n.js"), encoding="utf-8").read()

    def keys(name):
        m = re.search(r"const %s = \{(.*?)\n\};" % name, src, re.S)
        body = re.sub(r"'(?:[^'\\]|\\.)*'", "''", m.group(1))         # drop the texts, keep the keys
        return set(re.findall(r"([a-z_][a-z0-9_]*)\s*:", body))
    en, th = keys("en"), keys("th")
    assert en == th, f"only EN: {sorted(en - th)}; only TH: {sorted(th - en)}"
    for k in ("backup_now", "bin", "restore", "unlock", "duplicate", "cancel_task", "err_job_locked",
              "err_task_busy", "err_server_error", "health"):
        assert k in en, k
