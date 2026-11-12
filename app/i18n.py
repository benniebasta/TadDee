"""Thai text for the server's English messages (errors, warnings, task status).

The server writes plain English sentences. When the browser sends
`X-Lang: th`, the middleware in app/main.py passes every `detail`, `message`,
`warnings` and `error` string of a JSON reply through `translate()`.
Each rule is (regex on the English sentence, Thai template). Named groups are
put back into the Thai text; a group whose name starts with `t_` is itself
translated first (for nested sentences such as "<file>: <reason>").
A sentence with no rule stays English (tests/test_i18n_server.py lists the
sentences that must have a rule).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from contextvars import ContextVar

lang: ContextVar[str] = ContextVar("lang", default="en")

N = r"(?P<n>\d+)"
X = r"(?P<x>[\d.]+)"

_RULES: list[tuple[str, str]] = [
    # ---- nested: "<file name>: <reason>", "Sheet 2: <reason>", "Relief settings: ..."
    (r"Sheet (?P<s>\d+): (?P<t_rest>.+)", "แผ่นที่ {s}: {t_rest}"),
    (r"Relief settings: (?P<t_rest>.+)", "ค่างานนูน: {t_rest}"),
    (r"Backup failed: (?P<rest>.+)", "สำรองข้อมูลไม่สำเร็จ: {rest}"),
    (r"Unknown CAM setting: (?P<rest>.+)", "ค่าการกัดที่ไม่รู้จัก: {rest}"),

    # ---- general
    (r"(?P<t_what>.+) not found", "ไม่พบ{t_what}"),
    (r"Bad file name", "ชื่อไฟล์ไม่ถูกต้อง"),
    (r"Bad id", "รหัสไม่ถูกต้อง"),
    (r"Bad input", "ข้อมูลไม่ถูกต้อง"),
    (r"Page not found", "ไม่พบหน้านี้"),
    (r"Not Found", "ไม่พบ"),
    (r"Method Not Allowed", "ใช้คำสั่งนี้ไม่ได้"),
    (r"(?P<name>.+) is larger than 20 MB", "{name} ใหญ่เกิน 20 MB"),
    (r"Choose a file to upload", "เลือกไฟล์ที่จะอัปโหลด"),
    (r"Choose a picture", "เลือกรูป"),
    (r"Something went wrong on the server\. The details are in the log \(request (?P<id>\w+)\)\.",
     "เซิร์ฟเวอร์ขัดข้อง รายละเอียดอยู่ในบันทึก (คำขอ {id})"),
    (r"Unexpected error \((?P<e>\w+)\)\. Details are in the log\.",
     "เกิดข้อผิดพลาดที่ไม่คาดคิด ({e}) รายละเอียดอยู่ในบันทึก"),

    # ---- jobs, lock, bin, tasks
    (r"This job is marked as cut and locked\. Unlock it first to change it\.",
     "งานนี้ตัดแล้วและถูกล็อก ปลดล็อกก่อนจึงจะแก้ไขได้"),
    (r"This job is already marked as cut", "งานนี้ถูกบันทึกว่าตัดแล้ว"),
    (r"This job is not locked", "งานนี้ไม่ได้ถูกล็อก"),
    (r"Move the job to the bin before deleting it for good", "ย้ายงานไปถังขยะก่อน แล้วจึงลบถาวร"),
    (r"This job is already being nested", "งานนี้กำลังจัดวางอยู่"),
    (r"This job is being nested: wait for the nest to finish, then make the files",
     "งานนี้กำลังจัดวาง รอให้เสร็จก่อนแล้วจึงสร้างไฟล์"),
    (r"This job is being nested: wait for the nest to finish", "งานนี้กำลังจัดวาง รอให้เสร็จก่อน"),
    (r"This relief is still being made", "งานนูนนี้ยังสร้างไม่เสร็จ"),
    (r"Job deleted", "ลบงานแล้ว"),
    (r"Waiting in the queue", "รอคิว"),
    (r"Running", "กำลังทำงาน"),
    (r"Cancelling…", "กำลังยกเลิก…"),
    (r"Cancelled", "ยกเลิกแล้ว"),
    (r"Done", "เสร็จแล้ว"),
    (r"Interrupted: the server stopped while this was running\. Run it again\.",
     "ถูกขัดจังหวะ: เซิร์ฟเวอร์หยุดระหว่างทำงาน กรุณาเริ่มใหม่"),

    # ---- nesting
    (r"Rotations must be numbers of degrees", "มุมหมุนต้องเป็นตัวเลของศา"),
    (r"Choose a material or enter the sheet size", "เลือกวัสดุ หรือใส่ขนาดแผ่น"),
    (r"Add parts \(quantity above 0\) before nesting", "เพิ่มชิ้นงาน (จำนวนมากกว่า 0) ก่อนจัดวาง"),
    (r"Nest the job before making files", "จัดวางงานก่อนสร้างไฟล์"),
    (r"Nest the job before marking it as cut", "จัดวางงานก่อนบันทึกว่าตัดแล้ว"),
    (r"No sheets to nest on", "ไม่มีแผ่นวัสดุให้จัดวาง"),

    # ---- offcuts
    (r"Material not found", "ไม่พบวัสดุ"),
    (r"Machine not found", "ไม่พบเครื่อง"),
    (r"The remnant outline is not a valid closed shape", "เส้นรอบเศษวัสดุไม่ใช่รูปปิดที่ถูกต้อง"),
    (r"The remnant must be at least " + X + r" mm on both sides", "เศษวัสดุต้องยาวอย่างน้อย {x} มม. ทั้งสองด้าน"),
    (r"The remnant is larger than 20 m: check the drawing units", "เศษวัสดุใหญ่เกิน 20 เมตร: ตรวจหน่วยของแบบ"),
    (N + r" smaller shape\(s\) ignored: only the largest closed shape is the remnant",
     "ข้ามรูปเล็ก {n} รูป: ใช้เฉพาะรูปปิดที่ใหญ่ที่สุดเป็นเศษวัสดุ"),

    # ---- export, tools, feeds
    (r"Choose a material first: the G-code needs its thickness", "เลือกวัสดุก่อน: G-code ต้องใช้ความหนาของวัสดุ"),
    (r"Cut tool not found", "ไม่พบดอกตัด"),
    (r"Mark tool not found", "ไม่พบดอกแกะ/ขีด"),
    (r"Add a tool in Settings first", "เพิ่มดอกกัดในหน้าตั้งค่าก่อน"),
    (r"This material and bit already have feeds and speeds: edit that row",
     "วัสดุและดอกนี้มีค่าความเร็วอยู่แล้ว: แก้ไขแถวนั้นแทน"),
    (r"The (?P<bit>.+) cannot cut parts out: pick a flat bit as the cut tool \(a V-bit can be the mark tool\)",
     "{bit} ตัดชิ้นงานออกไม่ได้: เลือกดอกปากตรงเป็นดอกตัด (ดอกวีใช้เป็นดอกแกะได้)"),
    (r"The parts were nested (?P<g>[\d.]+) mm apart but the (?P<bit>.+) is (?P<d>[\d.]+) mm wide: "
     r"nest again with a gap of at least (?P<m>[\d.]+) mm, or pick a thinner bit",
     "ชิ้นงานถูกวางห่างกัน {g} มม. แต่ {bit} กว้าง {d} มม.: จัดวางใหม่โดยเว้นระยะอย่างน้อย {m} มม. "
     "หรือเลือกดอกที่เล็กกว่า"),
    (r"The parts were nested (?P<g>[\d.]+) mm apart, less than the (?P<k>[\d.]+) mm kerf: "
     r"nest again with a gap of at least (?P<m>[\d.]+) mm, or lower the kerf",
     "ชิ้นงานถูกวางห่างกัน {g} มม. น้อยกว่าความกว้างลำเลเซอร์ {k} มม.: จัดวางใหม่โดยเว้นระยะอย่างน้อย "
     "{m} มม. หรือลดค่าความกว้างลำ"),
    (N + r" part\(s\) were deleted after nesting and are left out: nest again",
     "ชิ้นงาน {n} ชิ้นถูกลบหลังจัดวางและไม่อยู่ในไฟล์: จัดวางใหม่"),
    (r"Parts or quantities changed after nesting \((?P<names>.+)\): these files use the old layout, nest again",
     "ชิ้นงานหรือจำนวนเปลี่ยนหลังจัดวาง ({names}): ไฟล์เหล่านี้ใช้การจัดวางเดิม กรุณาจัดวางใหม่"),
    (r"nothing to cut", "ไม่มีอะไรให้ตัด"),
    (r"Material thickness is 0, nothing to cut", "ความหนาวัสดุเป็น 0 ไม่มีอะไรให้ตัด"),
    (r"Part (?P<p>.+) is missing, skipped", "ไม่พบชิ้นงาน {p} ข้ามไป"),
    (r"Part (?P<p>.+) has broken geometry, skipped", "ชิ้นงาน {p} มีรูปทรงเสีย ข้ามไป"),
    (r"Part (?P<p>.+) has no area, skipped", "ชิ้นงาน {p} ไม่มีพื้นที่ ข้ามไป"),
    (r"A hole in (?P<p>.+) is smaller than the (?P<d>[\d.]+) mm bit, skipped",
     "รูใน {p} เล็กกว่าดอก {d} มม. ข้ามไป"),
    (r"Part (?P<p>.+) is too small for tabs, cut without tabs \(hold it down\)",
     "ชิ้นงาน {p} เล็กเกินกว่าจะใส่แท็บ ตัดแบบไม่มีแท็บ (ยึดชิ้นงานให้แน่น)"),

    # ---- import
    (r"Only SVG, DXF, PDF or AI files", "รับเฉพาะไฟล์ SVG, DXF, PDF หรือ AI"),
    (r"(?P<f>.+) is empty", "{f} เป็นไฟล์ว่าง"),
    (r"Could not read (?P<f>.+): it does not look like a valid (?P<k>\w+) file",
     "อ่าน {f} ไม่ได้: ไม่ใช่ไฟล์ {k} ที่ถูกต้อง"),
    (r"Could not read (?P<f>.+): the file is damaged or not a PDF", "อ่าน {f} ไม่ได้: ไฟล์เสียหรือไม่ใช่ PDF"),
    (r"No closed shapes found", "ไม่พบรูปปิดในไฟล์"),
    (r"Could not read the SVG: the file is damaged or not an SVG", "อ่าน SVG ไม่ได้: ไฟล์เสียหรือไม่ใช่ SVG"),
    (r"Could not read the DXF: the file is damaged or not a DXF", "อ่าน DXF ไม่ได้: ไฟล์เสียหรือไม่ใช่ DXF"),
    (r"(?P<f>.+) is an old Illustrator file without PDF content: save it again with \"Create PDF Compatible File\" "
     r"ticked, or export SVG/PDF",
     "{f} เป็นไฟล์ Illustrator รุ่นเก่าที่ไม่มีข้อมูล PDF: บันทึกใหม่โดยติ๊ก \"Create PDF Compatible File\" "
     "หรือส่งออกเป็น SVG/PDF"),
    (r"(?P<f>.+) is password protected: save it without a password", "{f} มีรหัสผ่าน: บันทึกใหม่แบบไม่มีรหัสผ่าน"),
    (r"(?P<f>.+) has only pictures \(raster\), no vector cut lines: export the cut lines as vector paths "
     r"\(SVG, DXF or a vector PDF\)",
     "{f} มีแต่รูปภาพ ไม่มีเส้นตัดแบบเวกเตอร์: ส่งออกเส้นตัดเป็นเวกเตอร์ (SVG, DXF หรือ PDF แบบเวกเตอร์)"),
    (N + r" text item\(s\) skipped: text must be converted to outlines",
     "ข้ามข้อความ {n} รายการ: ต้องแปลงตัวอักษรเป็นเส้น (Create Outlines) ก่อน"),
    (N + r" text item\(s\) skipped: text not converted to outlines \(use Create Outlines / Convert to Curves "
     r"before saving\)",
     "ข้ามข้อความ {n} รายการ: ตัวอักษรยังไม่ได้แปลงเป็นเส้น (ใช้ Create Outlines / Convert to Curves ก่อนบันทึก)"),
    (N + r" image\(s\) skipped: pictures cannot be cut, use vector shapes",
     "ข้ามรูปภาพ {n} รูป: รูปภาพตัดไม่ได้ ใช้รูปทรงเวกเตอร์"),
    (N + r" (?P<kind>\w+) item\(s\) skipped: not a supported shape", "ข้าม {kind} {n} รายการ: รูปทรงที่ไม่รองรับ"),
    (r"page (?P<p>[\d, ]+) has only pictures \(raster\), nothing to cut there",
     "หน้า {p} มีแต่รูปภาพ ไม่มีอะไรให้ตัด"),
    (N + r" pages were read, stacked top to bottom", "อ่าน {n} หน้า เรียงจากบนลงล่าง"),
    (N + r" open line\(s\) were kept as (?P<layer>score|engrave) marks",
     "เก็บเส้นเปิด {n} เส้นเป็นเส้น{t_layer}"),
    (N + r" open line\(s\) outside every part were dropped", "ทิ้งเส้นเปิด {n} เส้นที่อยู่นอกชิ้นงาน"),
    (N + r" open line\(s\) went past the edge of their part and were cut back to it",
     "เส้นเปิด {n} เส้นเลยขอบชิ้นงาน จึงตัดให้อยู่ในขอบ"),
    (r"(?P<p>.+) has both score and engrave lines; all were set to (?P<layer>score|engrave)",
     "{p} มีทั้งเส้นขีดและเส้นแกะ ตั้งทั้งหมดเป็นเส้น{t_layer}"),
    (r"score", "ขีด"),
    (r"engrave", "แกะ"),

    # ---- relief
    (r"The picture could not be read: use a PNG or JPG file", "อ่านรูปไม่ได้: ใช้ไฟล์ PNG หรือ JPG"),
    (r"styles must be JSON \(a list of region settings\)", "ค่ารูปแบบต้องเป็น JSON (รายการค่าของแต่ละสี)"),

    # ---- validation (pydantic) short phrases
    (r"(?P<field>[\w.]+): (?P<t_rest>.+)", "{field}: {t_rest}"),
    (r"Field required", "ต้องกรอก"),
    (r"Input should be greater than or equal to (?P<v>[\d.]+)", "ต้องมากกว่าหรือเท่ากับ {v}"),
    (r"Input should be greater than (?P<v>[\d.]+)", "ต้องมากกว่า {v}"),
    (r"Input should be less than or equal to (?P<v>[\d.]+)", "ต้องน้อยกว่าหรือเท่ากับ {v}"),
    (r"Input should be less than (?P<v>[\d.]+)", "ต้องน้อยกว่า {v}"),
    (r"Input should be a valid number.*", "ต้องเป็นตัวเลข"),
    (r"Input should be a valid integer.*", "ต้องเป็นจำนวนเต็ม"),
    (r"String should have at least 1 character", "ต้องไม่ว่าง"),
]

# names that appear inside "<what> not found"
_WORDS = {"File": "ไฟล์", "Job": "งาน", "Part": "ชิ้นงาน", "Tool": "ดอกกัด", "Material": "วัสดุ",
          "Machine": "เครื่อง", "Offcut": "เศษวัสดุ", "Relief": "งานนูน", "Task": "งาน (คิว)",
          "Feeds row": "แถวค่าความเร็ว", "Feed": "ค่าความเร็ว"}

_COMPILED: list[tuple[re.Pattern, str]] = [(re.compile(rf"^(?:{p})$", re.S), t) for p, t in _RULES]


def translate(text: str, to: str | None = None) -> str:
    """English sentence -> Thai when `to` (or the request language) is 'th'."""
    if (to or lang.get()) != "th" or not isinstance(text, str) or not text:
        return text
    return _th(text, 0)


def _th(text: str, depth: int) -> str:
    if depth > 4:
        return text
    if text in _WORDS:
        return _WORDS[text]
    for rx, tpl in _COMPILED:
        m = rx.match(text)
        if not m:
            continue
        vals = {}
        for k, v in m.groupdict().items():
            v = v or ""
            vals[k] = _th(v, depth + 1) if k.startswith("t_") else v
            if k == "layer":
                vals["t_layer"] = _th(v, depth + 1)
        return tpl.format(**vals)
    return text


def has_rule(text: str) -> bool:
    return _th(text, 0) != text


def translate_json(obj, tr: Callable[[str], str] = translate):
    """Translate the user-facing strings of a JSON reply in place and return it."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("detail", "message", "error") and isinstance(v, str):
                obj[k] = tr(v)
            elif k == "warnings" and isinstance(v, list):
                obj[k] = [tr(x) if isinstance(x, str) else x for x in v]
            elif isinstance(v, (dict, list)):
                translate_json(v, tr)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                translate_json(v, tr)
    return obj
