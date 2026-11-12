# Nest app: implementation plan

Our own nesting and CNC/laser output app for the AD ON group factory. It is not a copy of any other product. All code, UI text and design are ours. Learn from public behaviour only.

Status: this file is the build spec. Each section is written so one developer (or agent) can build their part without asking questions. When the code and this plan disagree, fix one of them in the same change.

---

## 1. Goal and users

Factory staff (sign shop, Bangkok) upload cut files from Illustrator/CorelDRAW (SVG or DXF), choose the material and sheet, and press **Nest**. The app packs the parts tightly on as few sheets as possible, shows the layout, and exports:
- **CNC router (NC Studio, no tool changer):** one `.nc` G-code file per sheet per bit. The operator loads the bit, sets Z0 on the material top, runs the file.
- **Laser (Ruida controller):** one DXF per sheet, with colour layers that the laser software maps to power and speed.

It also makes **3D relief carvings** from a flat picture (module `relief/`, already built and tested).

Non-goals for v1: user accounts and roles, payments, cloud hosting, direct Ruida `.rd` files, automatic tool changers, 4-axis, AI anything. G-code is produced by plain deterministic code.

Runs on the office PC (Windows, "TANINP") and is opened from other PCs and phones over the office network/Tailscale at `http://<pc>:8040`.

## 2. Tech stack (fixed)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | geometry libraries, same as the ERP |
| Geometry | shapely 2.x | polygons, offsets, validity, unions |
| File import | svgelements (SVG), ezdxf (DXF) | read curves, units, layers |
| Numerics | numpy, scipy (ndimage, signal.fftconvolve), Pillow | raster placement, relief |
| Laser output | ezdxf (R2010 DXF) | layers + ACI colours |
| API | FastAPI + uvicorn, python-multipart | typed JSON API, file upload |
| Database | SQLite through SQLAlchemy 2.0 (ORM, `Mapped[]` style), one file `data/nest.db` | no server to install |
| Web UI | Plain HTML + CSS + ES modules (no build step, no framework), served by FastAPI from `web/` | runs anywhere, nothing to compile on the PC |
| Tests | pytest (unit + API with `fastapi.testclient`), Playwright for Node (e2e, Chromium) | |

Python packages: `numpy scipy pillow matplotlib shapely pyclipper ezdxf svgelements fastapi uvicorn[standard] sqlalchemy python-multipart httpx pytest`. Nothing else without updating this table.

Start: `python -m app` (host 0.0.0.0, port 8040; env `NEST_PORT`, `NEST_DATA_DIR` default `./data`). Windows: `run.bat` does the same.

## 3. Folder layout and owners

```
core/      models.py (shared types, DONE), importers.py, svgout.py      -> Agent A
nesting/   engine.py                                                     -> Agent B
cam/       toolpaths.py, ncstudio.py, ruida.py                           -> Agent C
relief/    DONE: heightmap, toolpath, gcode, preview, api.run_relief     -> nobody (read only)
app/       __main__.py, main.py, db.py, models.py, schemas.py, services.py, seed.py -> Agent D
web/       index.html, style.css, app.js, api.js, views/*.js, i18n.js    -> Agent E
tests/     test_<module>.py, one file per owner                          -> each agent its own
samples/   parts.svg, parts.dxf, letters.svg (made by a script we own)   -> Agent A
e2e/       Playwright tests                                              -> lead (after build)
```
Rule: only edit files you own. If you need something from another module, code against the contract in section 4. Do not edit `core/models.py`; if a field is missing, put it in your own module and say so in your report.

## 4. Contracts between modules

### 4.1 Shared types (`core/models.py`, done, read it)
`Part`, `SheetSpec`, `NestSettings`, `Placement`, `SheetResult`, `NestResult`, `place()`, `placed_part()`.
- Units mm, Y up. A `Part.shape` is ONE valid shapely `Polygon` (holes allowed) normalised to `bounds[0:2] == (0, 0)`.
- A placement means: rotate the part about (0,0) by `rotation` degrees CCW, then translate by `(x, y)`. Always call `core.models.place()`; never re-implement it.
- Sheet frame: (0,0) bottom-left, X along the sheet width (the long side when the user enters 2440 x 1220 as width x height).

### 4.2 Importers (`core/importers.py`)
```python
class ImportError_(ValueError): ...          # message is shown to the user
@dataclass
class ImportReport:
    parts: list[Part]
    warnings: list[str]                      # plain sentences, e.g. "3 open lines were kept as engrave marks"
    units: str                               # "mm" | "inch" | "px" (what the file said)
def import_file(data: bytes, filename: str, *, tolerance_mm: float = 0.05,
                default_units: str = "mm") -> ImportReport
```
- Choose SVG or DXF by extension (`.svg`, `.dxf`, case-insensitive); anything else raises `ImportError_("Only SVG or DXF files")`.
- **Curves** are flattened to polylines with max chord error `tolerance_mm`.
- **SVG:** `svgelements.SVG.parse(BytesIO, ppi=96)`. Convert user units to mm: values are px at 96 ppi, so mm = px * 25.4 / 96 (svgelements already resolves `width="210mm"` + viewBox into px). Apply all transforms (`reify`). Flip Y (svg y down) so the result is Y up. Skip invisible elements (`display:none`, no fill and no stroke), `<text>` (warn: "text must be converted to outlines"), and `<image>` (warn).
- **DXF:** `ezdxf.read(StringIO)` (try utf-8 then cp874/latin-1). Units from `$INSUNITS` (1 inch, 4 mm, 5 cm, 6 m; 0/unknown -> `default_units`). Entities in modelspace: LWPOLYLINE, POLYLINE (2D), LINE, ARC, CIRCLE, ELLIPSE, SPLINE; explode INSERT blocks (`virtual_entities`). Join LINE/ARC/open polylines end to end when endpoints are within 0.02 mm into chains; a chain whose ends meet is closed.
- **Grouping into parts:** take all closed rings, make valid polygons (`shapely.make_valid`, keep Polygons, drop rings with area < 1 mm²). Sort by area descending; a ring inside another ring is a hole of it (even depth = outer, odd depth = hole). A ring at depth 2 (an island inside a hole) is its own new part. Result: each outer ring + its direct holes = one `Part`.
- **Open lines** (not closed): attach to the smallest part whose polygon contains the line's midpoint, as `marks` with `mark_layer="engrave"`; open lines outside every part are dropped with a warning. In SVG, a stroke colour of pure blue (#0000ff) means `mark_layer="score"`.
- Normalise each part (translate so bounds start at 0,0, also translate its marks the same). Name parts `<file stem>-<n>` in top-to-bottom, left-to-right order of the original file. `id` = short uuid4 hex (8 chars). `qty=1`.
- Empty result -> `ImportError_("No closed shapes found")`.
- Fail cleanly on corrupt files with `ImportError_` (never a raw stack trace).

### 4.3 SVG preview (`core/svgout.py`)
```python
def part_svg(part: Part, size_px: int = 80) -> str            # thumbnail, viewBox fitted, Y flipped for screen
def sheet_svg(sheet: SheetResult, parts: dict[str, Part], *, show_offcut: bool = True,
              labels: bool = True) -> str
```
- Pure strings, no external CSS. `viewBox="0 0 W H"` in mm with `transform="matrix(1 0 0 -1 0 H)"` on a group to flip Y. Sheet = light grey fill `#f4f5f7` with border; margin shown as a dashed line; each part a filled path (`fill-rule="evenodd"` so holes show), colour from a fixed 8-colour pastel palette by part index; marks as thin dark lines; offcut hatched light green with its size as text; optional small part labels (name) at the polygon's `representative_point()`, text not flipped (place text outside the flipped group).
- Each part `<path>` gets `data-part-id` and `data-copy`.

### 4.4 Nesting engine (`nesting/engine.py`)
```python
def nest(parts: list[Part], sheets: list[SheetSpec], settings: NestSettings) -> NestResult
def validate(result: NestResult, parts: list[Part], sheets: list[SheetSpec], settings: NestSettings) -> list[str]
    # [] when valid; otherwise human-readable problems (overlap, outside margin, gap too small)
```
Algorithm (raster no-fit placement, bottom-left-fill with gravity to -X):
1. Expand each part copy to instances (`part.qty` copies). Skip parts with qty 0.
2. Per part and rotation, compute `inflated = place(part.shape, rot, 0, 0).buffer(gap/2, join_style="round", quad_segs=8)`, then translate so its bounds start at (0,0); remember the offset so the final `Placement` refers to the uninflated part.
3. Sheet usable area = `sheet.polygon().buffer(-margin + gap/2)` (parts must keep `margin` from the edge; the inflation already adds gap/2, so subtract it back). If empty, the sheet is skipped.
4. Raster at `settings.resolution_mm` = r. Occupancy grid of the sheet: 1 = blocked (outside usable area or covered by an already placed inflated part), 0 = free. Rasterise with Pillow `ImageDraw.polygon` (exterior 1, holes 0) at r, then `binary_dilation` by 1 cell so rounding can never cause an overlap (conservative).
5. For an instance at rotation `rot`: kernel = rasterised inflated shape (dilated by 1 cell too). Collision map = `fftconvolve(occupancy, kernel[::-1, ::-1], mode="valid") > 0.5` (round and threshold; result index (i, j) = kernel origin at cell (i, j)). Free positions are the zeros. Pick the free position with the smallest x of the kernel's right edge, tie-break smallest y (gravity left then down, so the leftover is one strip at the +X end). If `allow_in_holes` is False, also block the holes of placed parts (rasterise exteriors only).
6. Try every rotation in `settings.rotations`, keep the best (smallest right edge, then smallest y). Convert the cell position back to mm, build the `Placement` (x, y include the offset from step 2), then do an **exact check** with shapely: the placed inflated polygon must be within the usable area and must not overlap (intersection area > 0.01 mm²) any placed inflated polygon. If the exact check fails (should not happen), mark those cells blocked and retry the next best position.
7. Fill order: instances by area descending (biggest first). Parts that fit no sheet in the list go to the next sheet. Sheets are used in the given order: offcuts first (the app lists them first when "use offcuts" is on), then the standard sheet repeated (`qty=None` means unlimited). A part larger than every sheet goes to `unplaced`.
8. Improvement loop until `time_limit_s` (default 10 s): with `random.Random(seed)`, try other orders (swap neighbours in the area order, or shuffle parts with similar area within 15%) and keep the best result by (fewer sheets, then smaller used length on the last sheet, then higher utilisation). The first attempt is always plain area-descending, so a result exists even with `time_limit_s=0`. Same input + seed = same output (no time-based randomness; the time limit only stops the loop, so on a fast PC you may get more attempts, which is fine).
9. Per sheet: `used_area` = sum of part areas (true shapes, holes subtracted), `utilisation = used_area / sheet.polygon().area`. Offcut: the rectangle from (max X of placed inflated parts + gap/2) to the usable max X, full usable height, if its width >= `min_offcut_mm` and height >= `min_offcut_mm`; else None.
10. `NestResult.utilisation` = total used area / total area of sheets in the result. Report `attempts`.

Performance target: 60 part instances on a 2440 x 1220 sheet, r = 2 mm, finishes the first attempt in < 5 s on a laptop. Cache kernels per (part, rotation). Use `numpy` float32.

### 4.5 2D CAM (`cam/`)
```python
@dataclass
class CamTool:
    id: str; name: str; diameter_mm: float
    kind: str = "flat"             # flat | ball | vbit
    angle_deg: float | None = None # vbit only
    feed_mm_min: float = 3000; plunge_mm_min: float = 800; rpm: int = 18000
    pass_depth_mm: float = 3.0     # max depth per pass

@dataclass
class CamSettings:
    thickness_mm: float            # material thickness
    cut_through_mm: float = 0.3    # go this far into the spoil board
    safe_z: float = 5.0
    climb: bool = True
    tabs: bool = True
    tab_width_mm: float = 6.0
    tab_height_mm: float = 1.5     # material left under the tab
    tab_min_part_mm: float = 0.0   # parts whose bbox min side is smaller than this get NO tabs (0 = all get tabs)
    engrave_depth_mm: float = 0.5  # for marks on layer engrave/score
    holes_first: bool = True

@dataclass
class ToolPath:                    # one continuous cut at one depth level, already ordered
    tool_id: str
    points: list[tuple[float, float, float]]   # x, y, z in mm, z <= 0 while cutting
    kind: str                      # "inside" | "outside" | "mark"
    part_id: str; copy: int

def sheet_toolpaths(sheet: SheetResult, parts: dict[str, Part], cut_tool: CamTool,
                    mark_tool: CamTool | None, s: CamSettings) -> tuple[list[ToolPath], list[str]]
    # returns (ordered paths, warnings)

@dataclass
class GcodeInfo: path: str; lines: int; cut_mm: float; minutes: float; z_min: float
def write_ncstudio(path: str, paths: list[ToolPath], tool: CamTool, *, title: str,
                   sheet_w: float, sheet_h: float, safe_z: float) -> GcodeInfo

def write_ruida_dxf(path: str, sheet: SheetResult, parts: dict[str, Part], *,
                    kerf_mm: float = 0.0, include_sheet_outline: bool = False) -> int   # returns entity count
```
CNC rules (`cam/toolpaths.py`):
- Holes: offset the hole ring inward by tool radius: `Polygon(hole).buffer(-r, join_style="round")`; take its exterior(s). If empty, warn "hole in <part> is smaller than the <d> mm bit, skipped". Outside: `Polygon(part.exterior).buffer(+r, join_style="round")` exterior.
- Direction (spindle turns clockwise): climb milling = outside path **clockwise**, inside path **counter-clockwise**. Conventional = the reverse. Use shapely `orient` / `is_ccw`.
- Start point: the ring vertex closest to the bottom-left of the sheet for outside cuts; for holes the vertex closest to the previous tool position.
- Depth levels: total = thickness + cut_through for profiles; n = ceil(total / pass_depth); levels evenly spaced, last = -total. Marks: single level at -engrave_depth.
- Tabs: only on the **last** level of outside profiles, only if `tabs` and the part is big enough per `tab_min_part_mm`. Count = clamp(round(perimeter / 400), 2, 8), centres evenly spaced by arc length, starting 1/2 spacing from the start point. Along a tab the Z is `-thickness + tab_height` (never below the level of the previous pass, never above 0); the tab length along the path = tab_width + tool diameter. Insert points exactly at tab start/end (split segments).
- Order on a sheet: all marks (with mark_tool, own file if a different tool), then all holes, then all outside profiles. A part nested inside another part's hole is cut completely before that hole (deepest nesting first), or it would be cut out of a loose slug. Inside each group, nearest-neighbour order starting from (0,0). Each part's levels are consecutive (all levels of one ring before moving on).
- Each `ToolPath` is one ring at one level, closed (first point == last point).
- Marks on layer engrave/score: follow the line (on-line) at engrave depth with `mark_tool` (if None use `cut_tool`).
- Warnings in plain words.

NC Studio writer (`cam/ncstudio.py`), same dialect as `relief/gcode.py` (read it):
- Header comments `(TITLE ...)`, `(TOOL <name> D<dia> ...)`, `(SHEET <w> x <h> mm, origin bottom-left, Z0 = material top)`; then `G21 G90 G17`, `G0 Z<safe>`, `M03 S<rpm>`, `G4 P3`.
- Per path: `G0 Z<safe>` (if down), `G0 X Y` to the start, `G1 Z<level> F<plunge>`, then `G1 ... F<feed>` and modal `X Y Z` lines. Between levels of the SAME ring (start point unchanged) go straight down with `G1 Z F<plunge>` without lifting.
- End: `G0 Z<safe>`, `M05`, `G0 X0 Y0`, `M30`. No M06, no T words. 3 decimals, trailing zeros stripped. CRLF line endings.
- Time estimate: cut length / feed + plunge length / plunge + rapid length / 6000.

Ruida DXF (`cam/ruida.py`): ezdxf R2010, `$INSUNITS = 4` (mm). Layers: `CUT` colour 1 (red), `SCORE` colour 5 (blue), `ENGRAVE` colour 7 (black/white), `SHEET` colour 8 (grey, only if `include_sheet_outline`). Each placed part: exterior and holes as closed LWPOLYLINEs on CUT (with `kerf_mm` > 0: offset exterior out and holes in by kerf/2); marks on SCORE or ENGRAVE by `mark_layer`. Entity colour = BYLAYER.

### 4.6 Relief (`relief/api.py`, done)
`ReliefParams` dataclass and `run_relief(image_path, out_dir, params, base=None) -> dict` (keys: width_mm, height_mm, depth_mm, lost_detail_mm, regions[], files[{name, tool, minutes, lines, deepest_z}], images[], total_minutes). Raises `ReliefError` with a user message. Region dicts: `index, color, area_mm2, profile, height_mm, round_mm, base_mm`. Takes a few seconds; call it in a worker thread (`run_in_threadpool`).

### 4.7 HTTP API (`app/`), all JSON unless noted, prefix `/api`
Errors: `{"detail": "<plain sentence>"}` with 400 (bad input), 404, 409. Money in THB as numbers, VAT not included (label it in the UI).

| Method + path | Body | Returns |
|---|---|---|
| GET `/health` | | `{"ok": true, "version": "0.1.0"}` |
| GET/POST `/machines`, GET/PUT/DELETE `/machines/{id}` | `{name, kind: "cnc"\|"laser", bed_w, bed_h, rate_per_hour, notes}` | machine |
| GET/POST `/tools`, PUT/DELETE `/tools/{id}` | CamTool fields minus id | tool |
| GET/POST `/materials`, PUT/DELETE `/materials/{id}` | `{name, thickness_mm, sheet_w, sheet_h, price_per_sheet, margin_mm}` | material |
| GET `/offcuts?material_id=` , DELETE `/offcuts/{id}` | | `[{id, material_id, width, height, outline_wkt, source_job_id, created_at, used}]` |
| GET `/jobs` | | `[{id, name, material, machine, status, sheets, utilisation, created_at}]` newest first |
| POST `/jobs` | `{name, material_id, machine_id}` | job detail |
| GET `/jobs/{id}` | | `{id, name, material, machine, status, parts: [PartOut], settings, nest: NestOut\|null, exports: [FileOut]}` |
| PATCH `/jobs/{id}` | any of `{name, material_id, machine_id}` | job detail |
| DELETE `/jobs/{id}` | | 204 |
| POST `/jobs/{id}/files` | multipart `file` (one or more) | `{parts: [PartOut], warnings: []}` (appended to the job) |
| PATCH `/jobs/{id}/parts/{part_id}` | `{qty?, name?, mark_layer?}` (qty 0..999) | PartOut |
| DELETE `/jobs/{id}/parts/{part_id}` | | 204 |
| POST `/jobs/{id}/nest` | `{gap_mm?, rotations?, allow_in_holes?, resolution_mm?, time_limit_s? (max 60), seed?, use_offcuts?, margin_mm?, sheet_w?, sheet_h?}` | NestOut |
| POST `/jobs/{id}/export` | `{target: "ncstudio"\|"ruida", cut_tool_id?, mark_tool_id?, cam?: {CamSettings fields minus thickness}, kerf_mm?}` | `{files: [FileOut], warnings: []}` (replaces earlier exports of the same target) |
| GET `/jobs/{id}/files/{name}` | | the file (download) |
| GET `/jobs/{id}/export.zip` | | zip of all exports |
| POST `/jobs/{id}/commit` | | marks job `cut`, saves sheet offcuts to `/offcuts`, marks used offcuts `used=true`; 409 if no nest |
| POST `/relief` | multipart `image` + form fields of ReliefParams (styles as JSON string) | `ReliefOut {id, ...run_relief dict, image_urls, file_urls}` |
| POST `/relief/{id}` | JSON ReliefParams fields (with `styles`) | ReliefOut (re-run on the stored image) |
| GET `/relief/{id}/files/{name}` | | file |

`PartOut = {id, name, qty, width, height, area_mm2, holes, marks, mark_layer, source, svg}` (svg = `part_svg`).
`NestOut = {sheets: [{index, sheet_id, width, height, is_offcut, utilisation, parts_count, svg, offcut: {width, height}|null}], unplaced: [{part_id, name, count}], utilisation, sheets_used, attempts, elapsed_s, cost: {sheets_thb, machine_thb, total_thb, cut_minutes, vat_included: false}}`. Cost: sheets_thb = full sheets used x material price (offcuts count 0); cut_minutes = sum of outside+inside perimeters x passes / cut tool feed (rough estimate before export); machine_thb = cut_minutes / 60 x machine rate.
`FileOut = {name, url, size, minutes?, tool?}`.
Job `status`: `draft` (parts only) -> `nested` -> `exported` -> `cut`. Re-nesting clears exports.

Storage (`app/models.py`, SQLAlchemy): tables `machines, tools, materials, jobs, job_parts (shape_wkt, marks_wkt, qty...), nest_runs (job_id, settings_json, result_json, created_at; keep latest per job), offcuts, relief_runs (id, image_path, params_json, result_json)`. Files under `<DATA_DIR>/jobs/<job_id>/` and `<DATA_DIR>/relief/<id>/`. File names safe: `<job>_<sheetNN>_<tool>.nc`, `<job>_<sheetNN>.dxf`; serve only names that exist in that folder (no path traversal: reject `/`, `\`, `..`). Upload limit 20 MB per file. Reload parts from WKT with `shapely.wkt.loads`.

Seed (`app/seed.py`, only when tables are empty): machines "Router (NC Studio)" cnc 2500x1300 rate 0; "Laser (Ruida)" laser 1300x900 rate 0. Tools: "6 mm flat 2-flute" 6 flat pass 3; "3 mm flat" 3 flat pass 2; "3 mm ball" 3 ball pass 1.5; "60° V-bit" 6 vbit angle 60 pass 1. Materials: "Acrylic 3 mm" 3 / 2440x1220, "ACP 3 mm" 3 / 2440x1220, "MDF 18 mm" 18 / 2440x1220, "PVC foam 5 mm" 5 / 2440x1220, margin 10, **price 0** (staff fill in real prices; the UI shows "set price" when 0; never invent prices).

### 4.8 Web UI (`web/`)
Single page, hash routes, plain ES modules, no CDN (works offline). Light theme only: white background, dark text, one accent colour (`#1f6feb`), soft grey borders, 8 px radius, system font stack plus "Noto Sans Thai". Big touch targets (min 40 px) for the shop floor. All numbers mm unless stated. Labels in English through `i18n.js` (`t(key)`) with a Thai dictionary ready (`th`); a language switch in the top bar stores the choice in `localStorage`.

Top bar: app name "Nest", nav: Jobs, Relief, Offcuts, Settings, language switch.

Views and **required `data-testid`s** (the e2e test depends on them, do not rename):
1. **Jobs** `#/jobs`: table of jobs (`jobs-table`, rows `job-row`), button **New job** (`new-job`) opens a form (`job-name`, `job-material` select, `job-machine` select, `job-create`). Click a row -> job page.
2. **Job** `#/jobs/:id`, three steps on one page:
   - **Parts:** drop zone + file input (`file-input`, accepts .svg,.dxf, multiple); parts table (`parts-table`, rows `part-row` with thumbnail svg, name, size W x H, holes, qty input `part-qty`, delete `part-delete`); import warnings box (`import-warnings`).
   - **Nest:** settings form: gap (`nest-gap`, default = cut tool diameter + 1 for cnc, 2 for laser), rotations select (`nest-rotations`: "0/180", "0/90/180/270" default, "every 45°", "every 15°"), parts in holes checkbox, quality select (`nest-quality`: Fast = 3 mm/5 s, Normal = 2 mm/10 s default, Fine = 1 mm/30 s), use offcuts checkbox (`nest-offcuts`), sheet size override (w/h), button **Nest** (`nest-run`, shows a spinner while waiting). Result: summary (`nest-summary`: sheets used, utilisation %, unplaced count, cost with "excl. VAT"), sheet previews (`sheet-preview`, one per sheet, the server SVG inline, zoom on click), unplaced list.
   - **Export:** for CNC machines: cut tool select (`export-tool`), mark tool select, cut-through, tabs on/off, tab size, climb on/off, button **Make G-code** (`export-ncstudio`). For laser: kerf, sheet outline checkbox, button **Make DXF** (`export-ruida`). Files list (`export-files`, links `export-file`) with minutes and tool, **Download all (zip)** (`export-zip`), and **Mark as cut** (`job-commit`, confirms with an in-page dialog, not `window.confirm`).
3. **Relief** `#/relief`: image input (`relief-image`), width (`relief-width`), material thickness (`relief-material`), max height (`relief-height`), rough bit (`relief-rough`), finish bit (`relief-finish`), button **Make relief** (`relief-run`). Result: preview image (`relief-preview`), regions table (`relief-regions`; per row colour swatch, profile select, height, round inputs) with **Update** (`relief-update`) that re-runs with edited styles; file links (`relief-files`), total minutes.
4. **Offcuts** `#/offcuts`: table by material (`offcuts-table`), delete buttons.
5. **Settings** `#/settings`: three editable tables (machines, tools, materials) with add/edit/delete (`settings-tools` table, `tool-add` button, form fields `tool-name`, `tool-diameter`, `tool-kind`, `tool-save`; same pattern for machines `machine-*` and materials `material-*` incl. `material-price`).
- Errors from the API show in a toast (`toast`), never `alert()`. Loading states disable buttons.
- `api.js` wraps fetch: JSON in/out, throws `Error(detail)`.

### 4.9 Safety guards (added in integration review)
- G-code export is refused when the nest gap is smaller than the cut bit diameter + 0.5 mm, or when the cut tool is a V-bit.
- Laser export is refused when the kerf is wider than the nest gap.
- Engrave depth is clamped to the profile depth. Pass depth is at least 0.1 mm.
- A sheet bigger than the machine bed is nested as the bed-sized piece of it.
- Marks that stick out of their part are cut back to the part outline.
- Export warns when parts or quantities changed after the last nest.

## 5. Features list (v1 acceptance)
1. Import SVG and DXF from Illustrator/CorelDRAW: curves, holes, islands, open lines as marks, units, warnings.
2. Part list per job with quantities and thumbnails.
3. True-shape nesting with gap, margin, rotations, parts in holes, offcuts first, multi-sheet, deterministic, time limit.
4. Sheet previews with utilisation, unplaced parts, leftover offcut size.
5. Cost estimate (sheets + machine time), THB excl. VAT.
6. CNC export: NC Studio G-code per sheet per bit, holes first, climb/conventional, multi-pass, tabs, marks engraving, time estimate.
7. Laser export: Ruida-ready DXF per sheet with CUT/SCORE/ENGRAVE layers and optional kerf.
8. Zip download of all files.
9. Offcut library: saved on "Mark as cut", reused by later nests.
10. Settings for machines, tools, materials (prices editable).
11. 3D relief from a picture (rules-based styles, editable), rough + finish G-code, previews.
12. English/Thai UI switch.

## 6. Quality bar and tests
- Unit tests per module in `tests/test_<module>.py`; run `python -m pytest -q` from the repo root. Must pass with no network.
- Importers: SVG with a ring + hole + island, Bezier curve, transform, mm units; DXF with LWPOLYLINE + bulge, CIRCLE, LINE chain forming a closed rectangle, INSUNITS inch; corrupt file -> ImportError_.
- Nesting: `validate()` returns [] on every test layout; no overlaps; margin respected; small part goes into a big part's hole when allowed and not when disallowed; too-big part -> unplaced; determinism (same seed twice -> identical placements); multi-sheet; offcut first.
- CAM: outside path is offset by r from the part (Hausdorff check); hole path inside; direction per climb flag; last level depth = -(thickness + cut_through); tab Z; marks depth; G-code has no M06/T, ends M30, max Z = safe_z; DXF layers and entity counts.
- API: TestClient flow create job -> upload sample SVG -> qty -> nest -> export ncstudio + ruida -> zip -> commit -> offcut listed; relief run on `samples/sign.png`; 404/400 paths; path traversal blocked.
- E2E (Playwright, Chromium at `/opt/pw-browsers/chromium`): the flows in section 5 through the UI.

## 7. Safety and ops
- Every G-code file starts at safe Z, has spindle on before cutting, ends with spindle off and M30. Z never goes below -(thickness + cut_through) for CAM, nor below the relief depth for relief.
- First real use: dry run (air cut) with Z0 set 20 mm above the material.
- Data folder is plain files + SQLite; back it up by copying `data/`.
- No secrets in the code. The app has no login in v1; keep it on the office network/Tailscale only.

## 8. Build order
1. Contracts (this file, `core/models.py`) — done.
2. In parallel: A importers + svgout + samples, B nesting, C CAM + writers, D backend, E web UI.
3. Integration review: run all tests, run the app, read every module boundary against section 4, fix mismatches.
4. E2E browser tests of all features, screenshots.
5. Repo push when Bennie names the repo; install on TANINP after his yes.

---

# Part 2. Production completion plan (2026-11-13)

**Where we are.** v1 works end to end in the test bench: import, true-shape nesting, NC Studio G-code, Ruida DXF, offcuts, cost, 3D relief, EN/TH workspace UI. 108 tests and an 18-step browser test pass. It has **never run on the real router or laser**, is not installed anywhere, has no login, no backup and no link to the ERP.
**Production quality means:** an operator can use it every day without me, a wrong file cannot reach a machine silently, nothing is lost if the PC dies, and every number on screen is one the shop can trust.

Order matters: P1 before anything else (real machines decide the rest). P2 and P3 can run side by side. P6 waits for the ERP cutover on 1 Dec.

## P1. Prove it on the real machines (blocker for everything)
| # | Work | Done when |
|---|---|---|
| 1.1 | Repo created, code pushed, installed on TANINP (run.bat), reachable over Tailscale on :8040 | Bennie opens it from his phone |
| 1.2 | NC Studio post check with one real file: header, units, arcs vs short lines, line endings, file extension, end-of-program, max line count | File loads in NC Studio with no warning and the on-screen path matches our preview |
| 1.3 | Air cut (bit above the sheet), then one cut in scrap: 100 × 100 mm square + 50 mm circle + one letter | Measured size within ±0.2 mm; tabs hold; holes cut before outlines |
| 1.4 | Ruida: DXF opened in the laser software, layer colours map to cut / score / engrave, scale 1:1 | Same test pieces within ±0.1 mm after the kerf setting |
| 1.5 | Relief: sample sign in foam or MDF with the shop's real bits | Depth and shape match the preview, no gouge, time within 20% of the estimate |
| 1.6 | Write the findings into machine profiles (post options per machine instead of fixed code) | Profiles saved, tests updated with the real file as a golden sample |

## P2. Close the function gaps (from the ENUT comparison, section 9 of the living doc)
| # | Work | Notes |
|---|---|---|
| 2.1 | Rotation: none, 30°, free (10° steps); grain lock per material | "None/180" forced for grain materials |
| 2.2 | Ramp entry (default 12 mm, along the path) and helical entry for small holes; lead-in position choice | Replaces straight plunge; option per tool |
| 2.3 | Thorough search: 40 s, custom up to 5 min, fine resolution 0.5 / 0.25 mm; runs as a background job with progress and Cancel | Needs 3.1 |
| 2.4 | Number of sets, mirror part, keep colours/materials on separate sheets | Colour read from the artwork |
| 2.5 | Nest into a hand-drawn remnant (any outline) and register a remnant by size from the phone | Uses the offcut store |
| 2.6 | Checks before export: corner too tight for the bit, slot narrower than the bit, part smaller than the tab rule, path outside the bed, Z below spoilboard limit | Shown on the canvas in red, export blocked or needs a tick |
| 2.7 | G-code simulator on the canvas: play, scrub, show rapid moves, per-pass depth colour, time | Reads our own file back, so it also tests the writer |
| 2.8 | Presets: letters/acrylic, panels, grain, max density | One click fills the nest settings |
| 2.9 | Feeds and speeds library: material × bit → feed, plunge, rpm, pass depth | Filled from the shop's real numbers (P1) |
| 2.10 | PDF and AI (PDF-compatible) import; text-not-outlined warning | SVG/DXF stay the safe path |

## P3. Make it reliable
| # | Work | Done when |
|---|---|---|
| 3.1 | Job queue: nesting and relief run in a worker, survive a browser close, one at a time per machine, progress shown | Close the tab mid-nest, reopen, result is there |
| 3.2 | Database migrations (Alembic) and a schema version check at start | Upgrade keeps old jobs |
| 3.3 | Export revisions: every export gets a number, old files kept, the file header carries job, sheet, revision, bit, material, date | Operator can prove which file was cut |
| 3.4 | Daily backup of the database and job files to `D:\Site backups\nest` (keep 14), plus a restore test | Restore on a clean folder passes |
| 3.5 | Logging to file with rotation, a health page, clear error messages in EN/TH, upload limits, clean-up of files older than 90 days (never offcuts or cut jobs) | No silent failures |
| 3.6 | Duplicate job, undo delete (30-day bin), lock a job after "Mark as cut" | No lost work by a wrong tap |

## P4. Access and safety
| # | Work | Notes |
|---|---|---|
| 4.1 | Login with two roles: operator (jobs, nest, export) and admin (machines, bits, materials, prices) | Only on Tailscale/LAN; no public exposure |
| 4.2 | Audit trail: who nested, exported, marked as cut, changed a bit or price | Shown on the job |
| 4.3 | Export sign-off: a checklist tick (bit fitted, sheet size, zero set) before download | Wording to confirm with the operators |
| 4.4 | Security review of upload parsing (SVG/DXF/PDF/images), path handling and dependencies | Report with fixes |

## P5. Deploy and hand over
| # | Work | Done when |
|---|---|---|
| 5.1 | Runs as a Windows service on TANINP, starts with the PC, restarts on crash | Reboot test passes |
| 5.2 | One-command update with automatic backup first and roll-back | Update and roll-back both tested |
| 5.3 | Automated tests on every push (unit, API, browser) + golden G-code/DXF samples from P1 + a benchmark set of 10 real jobs | Fails the push if a G-code byte changes without approval |
| 5.4 | Speed targets: 200 parts nested in under 30 s on TANINP; UI opens a 50-sheet job smoothly on the phone | Measured and recorded |
| 5.5 | Thai operator manual (one doc) and a 20-minute walk-through with the operators | Operators run a real job alone |
| 5.6 | Two-week parallel run beside the current way of working; issue list closed | Bennie's go-live yes |

## P6. Link to the ERP (after the 1 Dec cutover)
ERP job number on the nest job, materials and prices from the ERP, sheets and offcuts posted to stock, cutting time and cost back to the job. Read-only first, write after one clean week.

## Out of scope
ENUT's design tools (die-cut stickers, LED layout, letter pricing, plaques, QR, eyelets, edge unroll), automatic tool changer, writing Ruida `.rd` files directly, selling the app to other shops.

## Risks
1. NC Studio or the laser software reads our files differently from the spec; only P1 answers this.
2. Feeds, speeds and tab sizes are shop knowledge; wrong values break bits. Start slow, record what works.
3. Nesting density on real letter jobs may trail ENUT; the benchmark set (5.3) measures it before go-live.
4. TANINP is a single PC; the backup (3.4) is the only safety net.

## Needed from Bennie
1. Name of a new empty GitHub repo. 2. Yes to install on TANINP. 3. Five real job files and the usual sheet sizes and materials. 4. Bit list on the router and the exact laser software name. 5. Who will use it (names for logins). 6. A half-day slot at the factory for the P1 tests.
