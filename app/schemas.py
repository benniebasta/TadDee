"""Pydantic models for every request and response of the /api routes
(PLAN.md section 4.7). Lengths in mm, money in THB excl. VAT."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Pos = Field(gt=0)


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------- settings tables ----------

class MachineIn(_In):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["cnc", "laser"] = "cnc"
    bed_w: float = Field(default=0, ge=0)
    bed_h: float = Field(default=0, ge=0)
    rate_per_hour: float = Field(default=0, ge=0)
    notes: str = ""


class MachineUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["cnc", "laser"] | None = None
    bed_w: float | None = Field(default=None, ge=0)
    bed_h: float | None = Field(default=None, ge=0)
    rate_per_hour: float | None = Field(default=None, ge=0)
    notes: str | None = None


class MachineOut(BaseModel):
    id: int
    name: str
    kind: str
    bed_w: float
    bed_h: float
    rate_per_hour: float
    notes: str


class ToolIn(_In):
    name: str = Field(min_length=1, max_length=200)
    diameter_mm: float = Field(gt=0, le=100)
    kind: Literal["flat", "ball", "vbit"] = "flat"
    angle_deg: float | None = Field(default=None, gt=0, lt=180)
    feed_mm_min: float = Field(default=3000, gt=0)
    plunge_mm_min: float = Field(default=800, gt=0)
    rpm: int = Field(default=18000, gt=0)
    pass_depth_mm: float = Field(default=3.0, ge=0.1)
    ramp_mm: float = Field(default=12.0, ge=0, le=200)     # ramp entry length; 0 = straight plunge


class ToolUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    diameter_mm: float | None = Field(default=None, gt=0, le=100)
    kind: Literal["flat", "ball", "vbit"] | None = None
    angle_deg: float | None = Field(default=None, gt=0, lt=180)
    feed_mm_min: float | None = Field(default=None, gt=0)
    plunge_mm_min: float | None = Field(default=None, gt=0)
    rpm: int | None = Field(default=None, gt=0)
    pass_depth_mm: float | None = Field(default=None, ge=0.1)
    ramp_mm: float | None = Field(default=None, ge=0, le=200)


class ToolOut(BaseModel):
    id: int
    name: str
    diameter_mm: float
    kind: str
    angle_deg: float | None
    feed_mm_min: float
    plunge_mm_min: float
    rpm: int
    pass_depth_mm: float
    ramp_mm: float


# ---------- feeds and speeds (material x bit) ----------

class FeedIn(_In):
    material_id: int
    tool_id: int
    feed_mm_min: float | None = Field(default=None, gt=0)
    plunge_mm_min: float | None = Field(default=None, gt=0)
    rpm: int | None = Field(default=None, gt=0)
    pass_depth_mm: float | None = Field(default=None, ge=0.1)
    notes: str = ""


class FeedUpdate(_In):
    """PUT body: a field sent as null clears that override (back to the bit default)."""
    feed_mm_min: float | None = Field(default=None, gt=0)
    plunge_mm_min: float | None = Field(default=None, gt=0)
    rpm: int | None = Field(default=None, gt=0)
    pass_depth_mm: float | None = Field(default=None, ge=0.1)
    notes: str | None = None


class FeedOut(BaseModel):
    id: int
    material_id: int
    tool_id: int
    feed_mm_min: float | None
    plunge_mm_min: float | None
    rpm: int | None
    pass_depth_mm: float | None
    notes: str


class MaterialIn(_In):
    name: str = Field(min_length=1, max_length=200)
    thickness_mm: float = Field(gt=0)
    sheet_w: float = Field(gt=0)
    sheet_h: float = Field(gt=0)
    price_per_sheet: float = Field(default=0, ge=0)
    margin_mm: float = Field(default=10, ge=0)


class MaterialUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    thickness_mm: float | None = Field(default=None, gt=0)
    sheet_w: float | None = Field(default=None, gt=0)
    sheet_h: float | None = Field(default=None, gt=0)
    price_per_sheet: float | None = Field(default=None, ge=0)
    margin_mm: float | None = Field(default=None, ge=0)


class MaterialOut(BaseModel):
    id: int
    name: str
    thickness_mm: float
    sheet_w: float
    sheet_h: float
    price_per_sheet: float
    margin_mm: float


class OffcutOut(BaseModel):
    id: int
    material_id: int | None
    width: float
    height: float
    outline_wkt: str
    source_job_id: int | None
    created_at: str
    used: bool


# ---------- jobs ----------

class JobCreate(_In):
    name: str = Field(min_length=1, max_length=200)
    material_id: int | None = None
    machine_id: int | None = None


class JobUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    material_id: int | None = None
    machine_id: int | None = None


class JobListItem(BaseModel):
    id: int
    name: str
    material: str | None          # material name (None when not chosen)
    machine: str | None           # machine name
    material_id: int | None
    machine_id: int | None
    status: str
    sheets: int                   # sheets used by the latest nest, 0 before nesting
    utilisation: float | None     # 0..1 from the latest nest, None before nesting
    created_at: str
    deleted_at: str | None = None   # set when the job is in the bin
    purge_at: str | None = None     # when the bin deletes it for good


class PartOut(BaseModel):
    id: str
    name: str
    qty: int
    width: float
    height: float
    area_mm2: float
    holes: int                    # number of holes
    marks: int                    # number of mark lines (score/engrave)
    mark_layer: str
    source: str
    svg: str


class PartUpdate(_In):
    qty: int | None = Field(default=None, ge=0, le=999)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    mark_layer: Literal["score", "engrave"] | None = None


class UploadOut(BaseModel):
    parts: list[PartOut]
    warnings: list[str]


class NestIn(_In):
    gap_mm: float | None = Field(default=None, ge=0, le=100)
    rotations: list[float] | None = None
    allow_in_holes: bool | None = None
    resolution_mm: float | None = Field(default=None, ge=0.25, le=20)
    time_limit_s: float | None = Field(default=None, ge=0)      # capped at 60 by the server
    seed: int | None = None
    use_offcuts: bool | None = None
    margin_mm: float | None = Field(default=None, ge=0, le=500)
    sheet_w: float | None = Field(default=None, gt=0, le=20000)
    sheet_h: float | None = Field(default=None, gt=0, le=20000)


class NestSettingsOut(BaseModel):
    """The settings the job's latest nest used, or the defaults before nesting."""
    gap_mm: float
    rotations: list[float]
    allow_in_holes: bool
    resolution_mm: float
    time_limit_s: float
    seed: int
    use_offcuts: bool
    margin_mm: float
    sheet_w: float | None
    sheet_h: float | None


class OffcutSize(BaseModel):
    width: float
    height: float


class SheetOut(BaseModel):
    index: int
    sheet_id: str                 # "sheet" for a standard sheet, "offcut-<id>" for a stored offcut
    width: float
    height: float
    is_offcut: bool
    utilisation: float
    parts_count: int
    svg: str
    offcut: OffcutSize | None


class UnplacedOut(BaseModel):
    part_id: str
    name: str
    count: int


class CostOut(BaseModel):
    sheets_thb: float
    machine_thb: float
    total_thb: float
    cut_minutes: float
    vat_included: bool = False


class NestOut(BaseModel):
    sheets: list[SheetOut]
    unplaced: list[UnplacedOut]
    utilisation: float
    sheets_used: int
    attempts: int
    elapsed_s: float
    cost: CostOut


class FileOut(BaseModel):
    name: str
    url: str
    size: int
    minutes: float | None = None
    tool: str | None = None
    target: str | None = None     # "ncstudio" | "ruida" (relief files: None)
    sheet: int | None = None      # 1-based sheet number
    revision: int | None = None   # export revision of the job (1, 2, ...); None for relief files
    current: bool = True          # False: an older revision, or made before the latest nest
    created_at: str | None = None


class CamIn(_In):
    cut_through_mm: float | None = Field(default=None, ge=0, le=5)
    safe_z: float | None = Field(default=None, gt=0, le=100)
    climb: bool | None = None
    tabs: bool | None = None
    tab_width_mm: float | None = Field(default=None, ge=0)
    tab_height_mm: float | None = Field(default=None, ge=0)
    tab_min_part_mm: float | None = Field(default=None, ge=0)
    engrave_depth_mm: float | None = Field(default=None, ge=0)
    holes_first: bool | None = None
    lead_in: Literal["auto", "longest_edge_mid"] | None = None
    ramp_max_angle_deg: float | None = Field(default=None, ge=1, le=45)


class ExportIn(_In):
    target: Literal["ncstudio", "ruida"]
    cut_tool_id: int | None = None
    mark_tool_id: int | None = None
    cam: CamIn | None = None
    kerf_mm: float | None = Field(default=None, ge=0, le=5)
    include_sheet_outline: bool | None = None    # ruida only
    accept_warnings: bool = False                # make the files although checks found warnings


class ChecksQuery(_In):
    """GET /jobs/{id}/checks query: the same choices as the export body."""
    target: Literal["ncstudio", "ruida"] | None = None   # default: by the job's machine kind
    cut_tool_id: int | None = None
    mark_tool_id: int | None = None
    kerf_mm: float | None = Field(default=None, ge=0, le=5)
    cut_through_mm: float | None = Field(default=None, ge=0, le=5)
    safe_z: float | None = Field(default=None, gt=0, le=100)
    climb: bool | None = None
    tabs: bool | None = None
    tab_width_mm: float | None = Field(default=None, ge=0)
    tab_height_mm: float | None = Field(default=None, ge=0)
    tab_min_part_mm: float | None = Field(default=None, ge=0)
    engrave_depth_mm: float | None = Field(default=None, ge=0)
    holes_first: bool | None = None
    lead_in: Literal["auto", "longest_edge_mid"] | None = None
    ramp_max_angle_deg: float | None = Field(default=None, ge=1, le=45)


class CheckIssue(BaseModel):
    code: str
    severity: Literal["block", "warn"]
    message: str
    part_id: str | None
    sheet: int | None
    x: float | None
    y: float | None


class ChecksOut(BaseModel):
    target: str
    issues: list[CheckIssue]
    blocks: int
    warns: int


class ExportOut(BaseModel):
    files: list[FileOut]
    warnings: list[str]
    revision: int | None = None
    issues: list[CheckIssue] = []                # warnings the request accepted


class JobDetail(BaseModel):
    id: int
    name: str
    material: MaterialOut | None
    machine: MachineOut | None
    material_id: int | None
    machine_id: int | None
    status: str
    created_at: str
    parts: list[PartOut]
    settings: NestSettingsOut
    settings_saved: bool = False    # settings come from a nest (or a duplicated job), not defaults
    locked: bool = False            # marked as cut: no part, nest or export changes
    deleted_at: str | None = None
    nest: NestOut | None
    exports: list[FileOut]


class CommitOut(JobDetail):
    offcuts_saved: list[OffcutOut]
    offcuts_used: list[int]


# ---------- relief ----------

class ReliefIn(_In):
    """Re-run body: any ReliefParams field; missing ones keep the stored value."""
    width_mm: float | None = Field(default=None, gt=0)
    material_mm: float | None = Field(default=None, gt=0)
    max_height_mm: float | None = Field(default=None, gt=0)
    max_colors: int | None = Field(default=None, ge=2, le=16)
    resolution_mm: float | None = Field(default=None, gt=0)
    rough: str | None = None
    finish: str | None = None
    rough_feed: float | None = Field(default=None, gt=0)
    finish_feed: float | None = Field(default=None, gt=0)
    plunge_feed: float | None = Field(default=None, gt=0)
    rpm: int | None = Field(default=None, gt=0)
    stepdown_mm: float | None = Field(default=None, gt=0)
    allowance_mm: float | None = Field(default=None, ge=0)
    safe_z: float | None = Field(default=None, gt=0)
    styles: list[dict] | None = None


class ReliefFile(BaseModel):
    name: str
    tool: str
    minutes: float
    lines: int
    deepest_z: float


class ReliefOut(BaseModel):
    id: str
    width_mm: float
    height_mm: float
    depth_mm: float
    lost_detail_mm: float
    regions: list[dict]
    files: list[ReliefFile]
    images: list[str]
    total_minutes: float
    image_urls: list[str]         # same order as images
    file_urls: list[str]          # same order as files
    params: dict                  # the ReliefParams used (styles included)


# ---------- background tasks, backup, health ----------

class TaskOut(BaseModel):
    id: int
    task_id: int
    kind: str                     # nest | relief
    job_id: int | None
    ref: str | None               # relief run id
    status: str                   # queued | running | done | failed | cancelled
    progress: float               # 0..1
    message: str
    result: dict | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class BackupOut(BaseModel):
    time: str | None = None       # ISO time of the last backup, None = never
    path: str | None = None
    size: int | None = None
    folder: str | None = None     # where backups go
