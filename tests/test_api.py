"""API tests: the whole job flow through the HTTP routes, on a temporary
data folder. Run: python -m pytest -q tests/test_api.py"""
from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_SVG = os.path.join(ROOT, "samples", "parts.svg")
SIGN_PNG = os.path.join(ROOT, "samples", "sign.png")

# Fallback when the shared sample is missing: a 100 x 60 mm plate with a
# hole plus a 40 mm disc, in mm units.
TINY_SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">
  <path d="M10 10 H110 V70 H10 Z M40 40 A10 10 0 1 0 60 40 A10 10 0 1 0 40 40 Z" fill="#000" fill-rule="evenodd"/>
  <circle cx="160" cy="40" r="20" fill="#000"/>
</svg>"""


def sample_svg() -> tuple[str, bytes]:
    if os.path.isfile(SAMPLE_SVG):
        with open(SAMPLE_SVG, "rb") as fh:
            return "parts.svg", fh.read()
    return "tiny.svg", TINY_SVG


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "data"))
    with TestClient(app) as c:
        c.data_dir = str(tmp_path / "data")
        yield c


def by_name(rows, name):
    return next(r for r in rows if r["name"] == name)


def new_job(c, name="Test job", material="Acrylic 3 mm", machine="Router (NC Studio)"):
    mat = by_name(c.get("/api/materials").json(), material)
    mach = by_name(c.get("/api/machines").json(), machine)
    r = c.post("/api/jobs", json={"name": name, "material_id": mat["id"], "machine_id": mach["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def upload(c, jid, name=None, data=None):
    if name is None:
        name, data = sample_svg()
    r = c.post(f"/api/jobs/{jid}/files", files=[("file", (name, data, "image/svg+xml"))])
    assert r.status_code == 200, r.text
    return r.json()


FAST = {"resolution_mm": 3, "time_limit_s": 0, "seed": 1}


# ---------- basics ----------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": "0.1.0"}


def test_seed_present(client):
    machines = client.get("/api/machines").json()
    assert {(m["name"], m["kind"], m["bed_w"], m["bed_h"], m["rate_per_hour"]) for m in machines} == {
        ("Router (NC Studio)", "cnc", 2500, 1300, 0), ("Laser (Ruida)", "laser", 1300, 900, 0)}
    tools = {t["name"]: t for t in client.get("/api/tools").json()}
    assert set(tools) == {"6 mm flat 2-flute", "3 mm flat", "3 mm ball", "60° V-bit"}
    assert tools["60° V-bit"]["angle_deg"] == 60 and tools["60° V-bit"]["kind"] == "vbit"
    assert tools["3 mm ball"]["pass_depth_mm"] == 1.5
    mats = client.get("/api/materials").json()
    assert [m["name"] for m in mats] == ["Acrylic 3 mm", "ACP 3 mm", "MDF 18 mm", "PVC foam 5 mm"]
    assert all(m["price_per_sheet"] == 0 and m["margin_mm"] == 10 and m["sheet_w"] == 2440 for m in mats)


def test_seed_not_repeated(tmp_path):
    d = str(tmp_path / "d")
    create_app(d)
    c = TestClient(create_app(d))
    assert len(c.get("/api/tools").json()) == 4


def test_web_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert client.get("/api/nothing-here").status_code == 404


# ---------- settings CRUD ----------

def test_crud_tools(client):
    r = client.post("/api/tools", json={"name": "8 mm flat", "diameter_mm": 8, "kind": "flat", "pass_depth_mm": 4})
    assert r.status_code == 201
    t = r.json()
    assert t["feed_mm_min"] == 3000 and t["rpm"] == 18000
    r = client.put(f"/api/tools/{t['id']}", json={"name": "8 mm flat long", "feed_mm_min": 2500})
    assert r.status_code == 200 and r.json()["name"] == "8 mm flat long" and r.json()["diameter_mm"] == 8
    assert client.post("/api/tools", json={"name": "bad", "diameter_mm": -1}).status_code == 400
    assert client.post("/api/tools", json={"name": "bad", "diameter_mm": 3, "kind": "drill"}).status_code == 400
    assert client.delete(f"/api/tools/{t['id']}").status_code == 204
    assert client.delete(f"/api/tools/{t['id']}").status_code == 404
    assert client.put("/api/tools/9999", json={"name": "x"}).status_code == 404


def test_crud_materials(client):
    r = client.post("/api/materials", json={"name": "Foam 10", "thickness_mm": 10, "sheet_w": 1200,
                                            "sheet_h": 600, "price_per_sheet": 450})
    assert r.status_code == 201
    m = r.json()
    assert m["margin_mm"] == 10
    r = client.put(f"/api/materials/{m['id']}", json={"price_per_sheet": 500})
    assert r.json()["price_per_sheet"] == 500 and r.json()["sheet_w"] == 1200
    bad = client.post("/api/materials", json={"name": "x", "thickness_mm": 0, "sheet_w": 1, "sheet_h": 1})
    assert bad.status_code == 400 and isinstance(bad.json()["detail"], str)
    assert client.delete(f"/api/materials/{m['id']}").status_code == 204
    assert all(x["id"] != m["id"] for x in client.get("/api/materials").json())


def test_crud_machines(client):
    r = client.post("/api/machines", json={"name": "Small laser", "kind": "laser", "bed_w": 600, "bed_h": 400,
                                           "rate_per_hour": 300, "notes": "back room"})
    assert r.status_code == 201
    m = r.json()
    assert client.get(f"/api/machines/{m['id']}").json()["notes"] == "back room"
    r = client.put(f"/api/machines/{m['id']}", json={"rate_per_hour": 350})
    assert r.json()["rate_per_hour"] == 350 and r.json()["kind"] == "laser"
    assert client.post("/api/machines", json={"name": "x", "kind": "plasma"}).status_code == 400
    assert client.delete(f"/api/machines/{m['id']}").status_code == 204
    assert client.get(f"/api/machines/{m['id']}").status_code == 404


# ---------- jobs ----------

def test_create_job_and_list(client):
    j = new_job(client, "Shop sign")
    assert j["status"] == "draft" and j["parts"] == [] and j["nest"] is None and j["exports"] == []
    assert j["material"]["name"] == "Acrylic 3 mm" and j["machine"]["kind"] == "cnc"
    # default gap for cnc = smallest flat bit (3 mm) + 1
    assert j["settings"]["gap_mm"] == 4 and j["settings"]["sheet_w"] == 2440
    new_job(client, "Second")
    rows = client.get("/api/jobs").json()
    assert [r["name"] for r in rows] == ["Second", "Shop sign"]
    assert rows[1]["material"] == "Acrylic 3 mm" and rows[1]["sheets"] == 0 and rows[1]["utilisation"] is None
    r = client.patch(f"/api/jobs/{j['id']}", json={"name": "Shop sign v2"})
    assert r.status_code == 200 and r.json()["name"] == "Shop sign v2"
    assert client.patch(f"/api/jobs/{j['id']}", json={"material_id": 9999}).status_code == 400
    laser = by_name(client.get("/api/machines").json(), "Laser (Ruida)")
    r = client.patch(f"/api/jobs/{j['id']}", json={"machine_id": laser["id"]})
    assert r.json()["settings"]["gap_mm"] == 2
    assert client.delete(f"/api/jobs/{j['id']}").status_code == 204
    assert client.get(f"/api/jobs/{j['id']}").status_code == 404


def test_upload_and_parts(client):
    j = new_job(client)
    out = upload(client, j["id"])
    parts = out["parts"]
    assert parts and isinstance(out["warnings"], list)
    p = parts[0]
    assert set(p) >= {"id", "name", "qty", "width", "height", "area_mm2", "holes", "marks", "mark_layer",
                      "source", "svg"}
    assert p["svg"].lstrip().startswith("<svg") and p["qty"] == 1
    assert any(x["holes"] >= 1 for x in parts)
    r = client.patch(f"/api/jobs/{j['id']}/parts/{p['id']}", json={"qty": 3, "name": "Plate", "mark_layer": "score"})
    assert r.status_code == 200 and r.json()["qty"] == 3 and r.json()["name"] == "Plate"
    assert client.patch(f"/api/jobs/{j['id']}/parts/{p['id']}", json={"qty": 1000}).status_code == 400
    assert client.patch(f"/api/jobs/{j['id']}/parts/{p['id']}", json={"qty": -1}).status_code == 400
    assert client.patch(f"/api/jobs/{j['id']}/parts/nope", json={"qty": 1}).status_code == 404
    detail = client.get(f"/api/jobs/{j['id']}").json()
    assert len(detail["parts"]) == len(parts) and detail["parts"][0]["qty"] == 3
    # a second upload appends
    upload(client, j["id"], "tiny.svg", TINY_SVG)
    assert len(client.get(f"/api/jobs/{j['id']}").json()["parts"]) == len(parts) + 2
    last = parts[-1]
    assert client.delete(f"/api/jobs/{j['id']}/parts/{last['id']}").status_code == 204
    assert client.delete(f"/api/jobs/{j['id']}/parts/{last['id']}").status_code == 404


def test_upload_errors(client):
    j = new_job(client)
    r = client.post(f"/api/jobs/{j['id']}/files", files=[("file", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 400 and "SVG or DXF" in r.json()["detail"]
    r = client.post(f"/api/jobs/{j['id']}/files", files=[("file", ("bad.svg", b"<svg><<<", "image/svg+xml"))])
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)
    big = b"<svg>" + b" " * (20 * 1024 * 1024 + 10) + b"</svg>"
    r = client.post(f"/api/jobs/{j['id']}/files", files=[("file", ("big.svg", big, "image/svg+xml"))])
    assert r.status_code == 400 and "20 MB" in r.json()["detail"]
    assert client.post("/api/jobs/9999/files", files=[("file", ("a.svg", TINY_SVG, "image/svg+xml"))]).status_code == 404


def test_nest_needs_parts(client):
    j = new_job(client)
    r = client.post(f"/api/jobs/{j['id']}/nest", json=FAST)
    assert r.status_code == 400 and "parts" in r.json()["detail"].lower()
    assert client.post(f"/api/jobs/{j['id']}/export", json={"target": "ruida"}).status_code == 409
    assert client.post(f"/api/jobs/{j['id']}/commit").status_code == 409


# ---------- the full flow ----------

def test_full_flow(client):
    j = new_job(client, "Flow")
    jid = j["id"]
    parts = upload(client, jid)["parts"]
    client.patch(f"/api/jobs/{jid}/parts/{parts[0]['id']}", json={"qty": 2})

    r = client.post(f"/api/jobs/{jid}/nest", json={**FAST, "time_limit_s": 999})
    assert r.status_code == 200, r.text
    nest = r.json()
    assert set(nest) == {"sheets", "unplaced", "utilisation", "sheets_used", "attempts", "elapsed_s", "cost"}
    assert nest["sheets_used"] == len(nest["sheets"]) == 1
    sh = nest["sheets"][0]
    assert set(sh) == {"index", "sheet_id", "width", "height", "is_offcut", "utilisation", "parts_count",
                       "svg", "offcut"}
    assert sh["parts_count"] == len(parts) + 1 and sh["is_offcut"] is False
    assert "<svg" in sh["svg"] and "data-part-id" in sh["svg"]
    assert sh["offcut"] is not None and sh["offcut"]["width"] > 150
    assert nest["unplaced"] == []
    assert 0 < nest["utilisation"] < 1
    assert nest["cost"]["vat_included"] is False and nest["cost"]["sheets_thb"] == 0
    assert nest["cost"]["cut_minutes"] > 0
    detail = client.get(f"/api/jobs/{jid}").json()
    assert detail["status"] == "nested" and detail["nest"]["sheets_used"] == 1
    assert detail["settings"]["time_limit_s"] == 60          # capped
    assert client.get("/api/jobs").json()[0]["sheets"] == 1

    # CNC G-code
    tools = client.get("/api/tools").json()
    cut = by_name(tools, "6 mm flat 2-flute")
    vbit = by_name(tools, "60° V-bit")
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": cut["id"],
                                                    "mark_tool_id": vbit["id"],
                                                    "cam": {"cut_through_mm": 0.5, "tabs": True, "climb": True}})
    assert r.status_code == 200, r.text
    nc = r.json()
    names = [f["name"] for f in nc["files"]]
    assert len(names) == 2                                   # one per bit (marks + cut)
    assert all(n.endswith(".nc") and n.startswith("Flow_sheet01_") for n in names)
    assert any("V-bit" in n for n in names)
    f = next(x for x in nc["files"] if "2-flute" in x["name"])
    assert f["tool"] == "6 mm flat 2-flute" and f["minutes"] > 0 and f["size"] > 0
    assert f["url"] == f"/api/jobs/{jid}/files/{f['name']}"
    g = client.get(f["url"])
    assert g.status_code == 200
    text = g.text
    assert "M30" in text and "M06" not in text and "M03" in text

    # Laser DXF
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ruida", "kerf_mm": 0.1})
    assert r.status_code == 200, r.text
    dxf = r.json()["files"]
    assert [x["name"] for x in dxf] == ["Flow_sheet01.dxf"]
    d = client.get(dxf[0]["url"])
    assert d.status_code == 200 and b"CUT" in d.content
    detail = client.get(f"/api/jobs/{jid}").json()
    assert detail["status"] == "exported"
    assert sorted(x["name"] for x in detail["exports"]) == sorted(names + ["Flow_sheet01.dxf"])

    # exporting the same target again replaces its files, keeps the other target
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": cut["id"]})
    assert len(r.json()["files"]) == 1
    assert len(client.get(f"/api/jobs/{jid}").json()["exports"]) == 2

    # zip
    z = client.get(f"/api/jobs/{jid}/export.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert sorted(zf.namelist()) == sorted(x["name"] for x in client.get(f"/api/jobs/{jid}").json()["exports"])

    # path traversal and unknown names
    for bad in ["..%2F..%2Fnest.db", "%2E%2E", "..nest.db", "%2E%2E%2Fnest.db", "a%5Cb.nc", "nest.db",
                "does-not-exist.nc"]:
        rr = client.get(f"/api/jobs/{jid}/files/{bad}")
        assert rr.status_code in (400, 404), (bad, rr.status_code)
    assert client.get(f"/api/jobs/{jid}/files/..%5C..%5Cnest.db").status_code in (400, 404)

    # commit -> offcut saved
    r = client.post(f"/api/jobs/{jid}/commit")
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == "cut" and len(c["offcuts_saved"]) == 1
    oc = c["offcuts_saved"][0]
    assert oc["material_id"] == j["material_id"] and oc["source_job_id"] == jid and oc["used"] is False
    assert client.post(f"/api/jobs/{jid}/commit").status_code == 409
    listed = client.get(f"/api/offcuts?material_id={j['material_id']}").json()
    assert [o["id"] for o in listed] == [oc["id"]]
    assert set(listed[0]) == {"id", "material_id", "width", "height", "outline_wkt", "source_job_id",
                              "created_at", "used"}
    other = by_name(client.get("/api/materials").json(), "MDF 18 mm")
    assert client.get(f"/api/offcuts?material_id={other['id']}").json() == []

    # a new job on the same material uses the offcut first
    j2 = new_job(client, "Reuse")
    upload(client, j2["id"], "tiny.svg", TINY_SVG)
    r = client.post(f"/api/jobs/{j2['id']}/nest", json={**FAST, "use_offcuts": True})
    assert r.status_code == 200, r.text
    n2 = r.json()
    assert n2["sheets"][0]["is_offcut"] is True and n2["sheets"][0]["sheet_id"] == f"offcut-{oc['id']}"
    assert n2["sheets_used"] == 1 and n2["cost"]["sheets_thb"] == 0
    # without use_offcuts: a full sheet
    r = client.post(f"/api/jobs/{j2['id']}/nest", json={**FAST, "use_offcuts": False})
    assert r.json()["sheets"][0]["is_offcut"] is False
    # re-nest with offcut, commit -> offcut marked used
    client.post(f"/api/jobs/{j2['id']}/nest", json={**FAST, "use_offcuts": True})
    c2 = client.post(f"/api/jobs/{j2['id']}/commit").json()
    assert c2["offcuts_used"] == [oc["id"]]
    used = {o["id"]: o for o in client.get("/api/offcuts").json()}
    assert used[oc["id"]]["used"] is True
    assert client.get("/api/offcuts?used=false").json() == [o for o in client.get("/api/offcuts").json()
                                                            if not o["used"]]
    assert client.delete(f"/api/offcuts/{oc['id']}").status_code == 204
    assert client.delete(f"/api/offcuts/{oc['id']}").status_code == 404


def test_renest_clears_exports(client):
    j = new_job(client, "Again")
    jid = j["id"]
    upload(client, jid, "tiny.svg", TINY_SVG)
    client.post(f"/api/jobs/{jid}/nest", json=FAST)
    files = client.post(f"/api/jobs/{jid}/export", json={"target": "ruida"}).json()["files"]
    assert files and client.get(files[0]["url"]).status_code == 200
    r = client.post(f"/api/jobs/{jid}/nest", json={**FAST, "gap_mm": 8, "rotations": [0, 180]})
    assert r.status_code == 200
    d = client.get(f"/api/jobs/{jid}").json()
    assert d["exports"] == [] and d["status"] == "nested"
    assert d["settings"]["gap_mm"] == 8 and d["settings"]["rotations"] == [0, 180]
    assert client.get(files[0]["url"]).status_code == 404
    assert client.get(f"/api/jobs/{jid}/export.zip").status_code == 404


def test_nest_too_big_part_and_sheet_override(client):
    j = new_job(client, "Big")
    upload(client, j["id"], "tiny.svg", TINY_SVG)
    # a sheet too small for the 100 x 60 plate: it is unplaced, the disc fits
    r = client.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "sheet_w": 80, "sheet_h": 80, "margin_mm": 5,
                                                       "gap_mm": 2})
    assert r.status_code == 200, r.text
    n = r.json()
    assert len(n["unplaced"]) == 1 and n["unplaced"][0]["count"] == 1
    assert set(n["unplaced"][0]) == {"part_id", "name", "count"}
    assert n["sheets"][0]["width"] == 80


def test_job_without_material(client):
    r = client.post("/api/jobs", json={"name": "No material"})
    assert r.status_code == 201
    jid = r.json()["id"]
    upload(client, jid, "tiny.svg", TINY_SVG)
    r = client.post(f"/api/jobs/{jid}/nest", json=FAST)
    assert r.status_code == 400 and "sheet size" in r.json()["detail"]
    r = client.post(f"/api/jobs/{jid}/nest", json={**FAST, "sheet_w": 1000, "sheet_h": 500})
    assert r.status_code == 200
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio"})
    assert r.status_code == 400 and "material" in r.json()["detail"].lower()


def test_cost_uses_price_and_rate(client):
    mat = client.post("/api/materials", json={"name": "Priced", "thickness_mm": 3, "sheet_w": 1000,
                                              "sheet_h": 500, "price_per_sheet": 1200}).json()
    mach = client.post("/api/machines", json={"name": "Paid router", "kind": "cnc", "rate_per_hour": 600}).json()
    jid = client.post("/api/jobs", json={"name": "Cost", "material_id": mat["id"],
                                         "machine_id": mach["id"]}).json()["id"]
    upload(client, jid, "tiny.svg", TINY_SVG)
    cost = client.post(f"/api/jobs/{jid}/nest", json=FAST).json()["cost"]
    assert cost["sheets_thb"] == 1200
    assert cost["machine_thb"] == pytest.approx(cost["cut_minutes"] / 60 * 600, abs=1)
    assert cost["total_thb"] == pytest.approx(cost["sheets_thb"] + cost["machine_thb"], abs=0.01)


# ---------- 404s ----------

def test_not_found(client):
    for path in ["/api/jobs/999", "/api/machines/999", "/api/jobs/999/export.zip",
                 "/api/jobs/999/files/a.nc", "/api/relief/nope", "/api/relief/nope/files/preview.png"]:
        r = client.get(path)
        assert r.status_code == 404, path
        assert isinstance(r.json()["detail"], str)
    assert client.delete("/api/jobs/999").status_code == 404
    assert client.patch("/api/jobs/999", json={"name": "x"}).status_code == 404
    assert client.post("/api/jobs/999/nest", json={}).status_code == 404
    assert client.post("/api/jobs/999/commit").status_code == 404
    assert client.delete("/api/offcuts/999").status_code == 404
    assert client.delete("/api/materials/999").status_code == 404
    assert client.post("/api/relief/nope", json={}).status_code == 404


# ---------- relief ----------

@pytest.mark.skipif(not os.path.isfile(SIGN_PNG), reason="samples/sign.png missing")
def test_relief_run_and_rerun(client):
    with open(SIGN_PNG, "rb") as fh:
        img = fh.read()
    r = client.post("/api/relief", files={"image": ("sign.png", img, "image/png")},
                    data={"width_mm": "200", "material_mm": "18", "resolution_mm": "0.5"})
    assert r.status_code == 200, r.text
    out = r.json()
    for k in ("id", "width_mm", "height_mm", "depth_mm", "lost_detail_mm", "regions", "files", "images",
              "total_minutes", "image_urls", "file_urls", "params"):
        assert k in out, k
    assert out["width_mm"] == pytest.approx(200, abs=1)
    assert len(out["files"]) == 2 and len(out["file_urls"]) == 2
    assert out["file_urls"][0] == f"/api/relief/{out['id']}/files/{out['files'][0]['name']}"
    for u in out["image_urls"] + out["file_urls"]:
        assert client.get(u).status_code == 200, u
    assert client.get(f"/api/relief/{out['id']}/files/..%2F..%2Fnest.db").status_code in (400, 404)
    assert client.get(f"/api/relief/{out['id']}/files/missing.png").status_code == 404
    assert client.get(f"/api/relief/{out['id']}").json()["id"] == out["id"]

    # edit the styles: make the first raised region an engraving
    styles = [dict(rg) for rg in out["regions"]]
    target = next(i for i, rg in enumerate(styles) if rg["profile"] != "background")
    styles[target]["profile"] = "flat"
    styles[target]["height_mm"] = 2.0
    r = client.post(f"/api/relief/{out['id']}", json={"styles": styles})
    assert r.status_code == 200, r.text
    out2 = r.json()
    assert out2["id"] == out["id"]
    assert out2["regions"][target]["profile"] == "flat" and out2["regions"][target]["height_mm"] == 2.0
    assert out2["params"]["width_mm"] == 200 and out2["params"]["resolution_mm"] == 0.5

    # a bad edit is a plain 400
    r = client.post(f"/api/relief/{out['id']}", json={"styles": styles[:1]})
    assert r.status_code == 400 and "regions" in r.json()["detail"]
    r = client.post(f"/api/relief/{out['id']}", json={"rough": "drill"})
    assert r.status_code == 400


def test_relief_bad_input(client):
    r = client.post("/api/relief", files={"image": ("x.png", b"not a picture", "image/png")},
                    data={"width_mm": "200", "material_mm": "18"})
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)
    r = client.post("/api/relief", files={"image": ("x.png", b"x", "image/png")}, data={"material_mm": "18"})
    assert r.status_code == 400
    r = client.post("/api/relief", files={"image": ("x.png", b"x", "image/png")},
                    data={"width_mm": "200", "material_mm": "18", "styles": "not json"})
    assert r.status_code == 400 and "JSON" in r.json()["detail"]
