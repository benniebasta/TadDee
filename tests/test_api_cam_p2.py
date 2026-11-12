"""Part 2 API tests: feeds and speeds library (2.9), pre-export checks (2.6),
export revisions (3.3), tool ramp setting (2.2)."""
from __future__ import annotations

import os
import re

from test_api import FAST, by_name, client, new_job, upload  # noqa: F401  (fixture)

# 100 x 60 mm plate with a 3 mm slot, 30 mm deep, from the top edge
SLOT_SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">
  <path d="M10 10 H110 V70 H61.5 V40 H58.5 V70 H10 Z" fill="#000"/>
</svg>"""

PLATE_SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">
  <path d="M10 10 H110 V70 H10 Z" fill="#000"/>
</svg>"""


def tool_id(c, name="6 mm flat 2-flute"):
    return by_name(c.get("/api/tools").json(), name)["id"]


def nested_job(c, svg=PLATE_SVG, name="P2"):
    j = new_job(c, name)
    upload(c, j["id"], "p.svg", svg)
    r = c.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "gap_mm": 7})
    assert r.status_code == 200, r.text
    return j


# ---------------------------------------------------------------- tools

def test_tool_ramp_setting(client):
    tools = client.get("/api/tools").json()
    assert all(t["ramp_mm"] == 12 for t in tools)              # default 12 mm ramp
    t = client.post("/api/tools", json={"name": "plunge bit", "diameter_mm": 4, "ramp_mm": 0}).json()
    assert t["ramp_mm"] == 0
    assert client.put(f"/api/tools/{t['id']}", json={"ramp_mm": 20}).json()["ramp_mm"] == 20
    assert client.put(f"/api/tools/{t['id']}", json={"ramp_mm": -1}).status_code == 400


# ---------------------------------------------------------------- feeds

def test_feeds_crud(client):
    mat = by_name(client.get("/api/materials").json(), "Acrylic 3 mm")["id"]
    tid = tool_id(client)
    assert client.get("/api/feeds").json() == []               # nothing seeded
    r = client.post("/api/feeds", json={"material_id": mat, "tool_id": tid, "feed_mm_min": 1500, "rpm": 16000})
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["feed_mm_min"] == 1500 and f["plunge_mm_min"] is None and f["pass_depth_mm"] is None
    assert client.post("/api/feeds", json={"material_id": mat, "tool_id": tid}).status_code == 409
    assert client.post("/api/feeds", json={"material_id": 999, "tool_id": tid}).status_code == 404
    assert client.post("/api/feeds", json={"material_id": mat, "tool_id": 999}).status_code == 404
    assert client.post("/api/feeds", json={"material_id": mat, "tool_id": tid, "rpm": 0}).status_code == 400
    assert [x["id"] for x in client.get(f"/api/feeds?material_id={mat}").json()] == [f["id"]]
    assert client.get(f"/api/feeds?tool_id={tid + 100}").json() == []
    u = client.put(f"/api/feeds/{f['id']}", json={"plunge_mm_min": 400, "rpm": None}).json()
    assert u["plunge_mm_min"] == 400 and u["rpm"] is None and u["feed_mm_min"] == 1500
    assert client.get(f"/api/feeds/{f['id']}").json() == u
    assert client.delete(f"/api/feeds/{f['id']}").status_code == 204
    assert client.get(f"/api/feeds/{f['id']}").status_code == 404
    # deleting a bit drops its rows
    f2 = client.post("/api/feeds", json={"material_id": mat, "tool_id": tid, "feed_mm_min": 999}).json()
    client.delete(f"/api/tools/{tid}")
    assert client.get(f"/api/feeds/{f2['id']}").status_code == 404


def test_export_uses_feed_override_for_job_material(client):
    j = nested_job(client)
    jid, tid = j["id"], tool_id(client)
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid})
    text = client.get(r.json()["files"][0]["url"]).text
    assert "F3000" in text and "M03 S18000" in text           # bit defaults
    client.post("/api/feeds", json={"material_id": j["material_id"], "tool_id": tid,
                                    "feed_mm_min": 1234, "plunge_mm_min": 321, "rpm": 12000, "pass_depth_mm": 1})
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid})
    text = client.get(r.json()["files"][0]["url"]).text
    assert "F1234" in text and "F3000" not in text and "M03 S12000" in text and "F321" in text
    assert "FEED 1234 - PLUNGE 321 - RPM 12000 - PASS 1" in text
    # 3.3 mm total at 1 mm per pass: 4 levels of 0.825 mm
    zs = sorted({float(z) for z in re.findall(r"Z(-\d+(?:\.\d+)?)", text)})
    assert min(zs) == -3.3 and -0.825 in zs and -1.65 in zs
    # another material: no override, bit defaults again
    mdf = by_name(client.get("/api/materials").json(), "MDF 18 mm")["id"]
    client.patch(f"/api/jobs/{jid}", json={"material_id": mdf})
    client.post(f"/api/jobs/{jid}/nest", json={**FAST, "gap_mm": 7})
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid})
    assert "F3000" in client.get(r.json()["files"][0]["url"]).text


# ---------------------------------------------------------------- checks

def test_checks_endpoint_and_export_gate(client):
    j = nested_job(client, SLOT_SVG, "Slot")
    jid, tid = j["id"], tool_id(client)
    r = client.get(f"/api/jobs/{jid}/checks?cut_tool_id={tid}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["target"] == "ncstudio" and out["blocks"] == 0 and out["warns"] >= 1
    slot = next(i for i in out["issues"] if i["code"] == "slot_too_narrow")
    assert set(slot) == {"code", "severity", "message", "part_id", "sheet", "x", "y"}
    assert slot["severity"] == "warn" and slot["sheet"] == 1 and slot["x"] is not None

    # warn only: refused with 422 and the list, unless accepted
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid})
    assert r.status_code == 422
    body = r.json()
    assert "slot" in body["detail"] and any(i["code"] == "slot_too_narrow" for i in body["issues"])
    assert client.get(f"/api/jobs/{jid}").json()["exports"] == []       # nothing written
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid,
                                                    "accept_warnings": True})
    assert r.status_code == 200, r.text
    assert any(i["code"] == "slot_too_narrow" for i in r.json()["issues"])

    # a block always stops, accepted warnings or not
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid,
                                                    "accept_warnings": True, "cam": {"cut_through_mm": 2}})
    assert r.status_code == 422 and any(i["code"] == "too_deep" and i["severity"] == "block"
                                        for i in r.json()["issues"])
    assert client.get(f"/api/jobs/{jid}/checks?cut_tool_id={tid}&cut_through_mm=2").json()["blocks"] == 1


def test_checks_bed_and_laser(client):
    j = nested_job(client)
    jid, mid = j["id"], j["machine_id"]
    assert client.get(f"/api/jobs/{jid}/checks").json()["issues"] == []
    client.put(f"/api/machines/{mid}", json={"bed_w": 60, "bed_h": 60})      # machine swapped after nesting
    out = client.get(f"/api/jobs/{jid}/checks").json()
    assert out["blocks"] >= 1 and {i["code"] for i in out["issues"]} >= {"outside_bed"}
    r = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "accept_warnings": True})
    assert r.status_code == 422
    out = client.get(f"/api/jobs/{jid}/checks?target=ruida&kerf_mm=0.2").json()
    assert out["target"] == "ruida" and [i["code"] for i in out["issues"]] == ["outside_bed"]
    assert client.get("/api/jobs/999/checks").status_code == 404
    assert client.get(f"/api/jobs/{jid}/checks?lead_in=bad").status_code == 400
    j2 = new_job(client, "Not nested")
    assert client.get(f"/api/jobs/{j2['id']}/checks").status_code == 409


# ---------------------------------------------------------------- revisions

def test_export_revisions_increment_and_old_files_remain(client):
    j = nested_job(client, name="Rev")
    jid, tid = j["id"], tool_id(client)
    r1 = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid}).json()
    r2 = client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": tid,
                                                     "cam": {"lead_in": "longest_edge_mid"}}).json()
    r3 = client.post(f"/api/jobs/{jid}/export", json={"target": "ruida"}).json()
    assert (r1["revision"], r2["revision"], r3["revision"]) == (1, 2, 3)
    f1, f2 = r1["files"][0], r2["files"][0]
    assert f1["name"].endswith("_r1.nc") and f2["name"].endswith("_r2.nc") and f1["revision"] == 1
    assert r3["files"][0]["name"] == "Rev_sheet01_r3.dxf"
    folder = os.path.join(client.data_dir, "jobs", str(jid))
    assert sorted(os.listdir(folder)) == ["rev001", "rev002", "rev003"]
    assert os.listdir(os.path.join(folder, "rev001")) == [f1["name"]]

    exports = client.get(f"/api/jobs/{jid}").json()["exports"]
    assert [x["revision"] for x in exports] == [3, 2, 1]                  # newest first
    assert {x["name"]: x["current"] for x in exports} == {f1["name"]: False, f2["name"]: True,
                                                         r3["files"][0]["name"]: True}
    old = client.get(f1["url"])
    assert old.status_code == 200
    head = old.text.splitlines()[:9]
    assert "(JOB Rev - ID %d)" % jid in head and "(SHEET 1 OF 1 - REVISION 1)" in head
    assert any(h.startswith("(MATERIAL Acrylic 3 mm - THICKNESS 3 mm)") for h in head)
    assert any(h.startswith("(BIT 6 mm flat 2-flute") for h in head)
    assert any(re.match(r"\(DATE \d{4}-\d\d-\d\d \d\d:\d\d - Nest \d", h) for h in head)
    assert "REVISION 2" in client.get(f2["url"]).text

    # a refused export does not use up a revision number
    wide = client.post("/api/tools", json={"name": "10 mm", "diameter_mm": 10}).json()["id"]
    assert client.post(f"/api/jobs/{jid}/export", json={"target": "ncstudio", "cut_tool_id": wide}).status_code == 400
    assert client.post(f"/api/jobs/{jid}/export", json={"target": "ruida"}).json()["revision"] == 4

    # re-nest: files stay, none current; the next export is revision 5
    client.post(f"/api/jobs/{jid}/nest", json={**FAST, "gap_mm": 8})
    d = client.get(f"/api/jobs/{jid}").json()
    assert len(d["exports"]) == 4 and not any(x["current"] for x in d["exports"])
    assert client.get(f1["url"]).status_code == 200
    assert client.post(f"/api/jobs/{jid}/export", json={"target": "ruida"}).json()["revision"] == 5
    # deleting the job removes every revision
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert not os.path.exists(folder)
