"""Server messages come back in Thai when the browser asks for it (X-Lang: th)."""
import re

import pytest
from fastapi.testclient import TestClient

from app.i18n import has_rule, translate
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "data"), backup_dir=str(tmp_path / "bk"))) as c:
        yield c

SAMPLES = [
    "Job not found", "File not found", "Bad file name", "letters.svg is larger than 20 MB",
    "This job is marked as cut and locked. Unlock it first to change it.",
    "This job is already marked as cut", "This job is not locked",
    "Move the job to the bin before deleting it for good", "This job is already being nested",
    "This job is being nested: wait for the nest to finish, then make the files",
    "This relief is still being made", "Waiting in the queue", "Cancelled", "Done", "Running",
    "Interrupted: the server stopped while this was running. Run it again.",
    "Rotations must be numbers of degrees", "Choose a material or enter the sheet size",
    "Add parts (quantity above 0) before nesting", "Nest the job before making files",
    "Nest the job before marking it as cut", "Material not found", "Machine not found",
    "The remnant outline is not a valid closed shape", "The remnant must be at least 50 mm on both sides",
    "The remnant is larger than 20 m: check the drawing units",
    "2 smaller shape(s) ignored: only the largest closed shape is the remnant",
    "Choose a material first: the G-code needs its thickness", "Cut tool not found", "Add a tool in Settings first",
    "This material and bit already have feeds and speeds: edit that row",
    "The 60° V-bit cannot cut parts out: pick a flat bit as the cut tool (a V-bit can be the mark tool)",
    "The parts were nested 5 mm apart but the 6 mm flat is 6 mm wide: nest again with a gap of at least 7 mm, "
    "or pick a thinner bit",
    "The parts were nested 1 mm apart, less than the 2 mm kerf: nest again with a gap of at least 2 mm, "
    "or lower the kerf",
    "3 part(s) were deleted after nesting and are left out: nest again",
    "Parts or quantities changed after nesting (A, B): these files use the old layout, nest again",
    "Sheet 2: nothing to cut", "Sheet 1: Part A is too small for tabs, cut without tabs (hold it down)",
    "Sheet 1: A hole in letters-1 is smaller than the 6 mm bit, skipped",
    "Only SVG, DXF, PDF or AI files", "logo.svg: No closed shapes found",
    "logo.dxf: Could not read the DXF: the file is damaged or not a DXF",
    "logo.pdf: logo.pdf is password protected: save it without a password",
    "3 text item(s) skipped: text must be converted to outlines",
    "1 image(s) skipped: pictures cannot be cut, use vector shapes",
    "2 open line(s) were kept as score marks", "1 open line(s) outside every part were dropped",
    "The picture could not be read: use a PNG or JPG file", "Choose a picture", "Choose a file to upload",
    "qty: Input should be less than or equal to 999", "Relief settings: width_mm: Field required",
]


@pytest.mark.parametrize("msg", SAMPLES)
def test_every_message_has_thai(msg):
    th = translate(msg, "th")
    assert has_rule(msg) and re.search("[฀-๿]", th), (msg, th)
    assert translate(msg, "en") == msg


def test_numbers_and_names_survive():
    assert translate("Sheet 3: Part Logo A has no area, skipped", "th") == "แผ่นที่ 3: ชิ้นงาน Logo A ไม่มีพื้นที่ ข้ามไป"


def test_api_answers_in_thai(client):
    r = client.get("/api/jobs/999", headers={"X-Lang": "th"})
    assert r.status_code == 404 and r.json()["detail"] == "ไม่พบงาน"
    assert client.get("/api/jobs/999").json()["detail"] == "Job not found"
    j = client.post("/api/jobs", json={"name": "T"}).json()
    r = client.post(f"/api/jobs/{j['id']}/nest", json={}, headers={"X-Lang": "th"})
    assert r.status_code == 400 and re.search("[฀-๿]", r.json()["detail"])
