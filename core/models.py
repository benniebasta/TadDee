"""Shared data types. Every module talks through these. Units: millimetres,
angles in degrees, X right, Y UP (DXF convention). Do not change a field
without updating PLAN.md section 4."""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry

LAYERS = ("cut", "score", "engrave")


@dataclass
class Part:
    """One cut-out shape. shape is a single valid Polygon (holes allowed),
    normalised so shape.bounds[0] == 0 and shape.bounds[1] == 0."""
    id: str
    name: str
    shape: Polygon
    qty: int = 1
    marks: list[LineString] = field(default_factory=list)  # open lines inside the part (score/engrave), same frame as shape
    mark_layer: str = "engrave"                            # layer of the marks: score | engrave
    source: str = ""                                       # file name it came from
    mirror: bool = False                                   # cut it mirrored (flipped left-right) everywhere
    color: str = ""                                        # "#rrggbb" from the artwork (fill, else stroke), "" = unknown

    @property
    def area(self) -> float:
        return float(self.shape.area)

    @property
    def width(self) -> float:
        return float(self.shape.bounds[2] - self.shape.bounds[0])

    @property
    def height(self) -> float:
        return float(self.shape.bounds[3] - self.shape.bounds[1])


@dataclass
class SheetSpec:
    """A sheet or offcut to nest on. If outline is given (offcut of any
    shape), it is used instead of the width x height rectangle; the outline
    is in the sheet's own frame with (0,0) at its bottom-left."""
    id: str
    width: float
    height: float
    margin: float = 10.0
    qty: int | None = None           # None = as many as needed
    outline: Polygon | None = None
    is_offcut: bool = False
    name: str = ""

    def polygon(self) -> Polygon:
        if self.outline is not None:
            return self.outline
        return Polygon([(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)])


@dataclass
class NestSettings:
    gap_mm: float = 6.0                      # min distance between parts (CNC: bit diameter + ~1 mm)
    rotations: list[float] = field(default_factory=lambda: [0.0, 90.0, 180.0, 270.0])
    allow_in_holes: bool = True              # small parts may go inside holes of big ones
    resolution_mm: float = 2.0               # raster cell size used by the placer
    time_limit_s: float = 10.0               # stop trying new orders after this
    seed: int = 1                            # same seed + same input = same layout
    min_offcut_mm: float = 150.0             # leftover strips narrower than this are waste
    separate_colors: bool = False            # parts of different Part.color never share a sheet
    max_grid_cells: int = 12_000_000         # finer resolutions are coarsened so one sheet grid stays this small


@dataclass
class Placement:
    """Where one copy of a part goes: if mirror, flip x -> -x about (0,0) first;
    then rotate about (0,0) by rotation degrees (counter-clockwise), then
    translate by (x, y). Use place() / placed_part() to apply."""
    part_id: str
    copy: int          # 0..qty-1
    x: float
    y: float
    rotation: float
    mirror: bool = False


@dataclass
class SheetResult:
    sheet_id: str
    index: int                 # 0-based order of sheets in the result
    width: float
    height: float
    placements: list[Placement]
    used_area: float           # sum of part areas on this sheet
    utilisation: float         # used_area / sheet area, 0..1
    offcut: Polygon | None = None   # reusable leftover (usually a strip at the +X end), or None
    is_offcut: bool = False


@dataclass
class NestResult:
    sheets: list[SheetResult]
    unplaced: dict[str, int]   # part_id -> copies that did not fit anywhere
    utilisation: float         # total used area / total area of used sheets
    elapsed_s: float
    attempts: int = 1
    # How the run went: resolution_mm (used), resolution_requested, rotations (used),
    # stopped ("done" | "time" | "cancel" | "exhausted"), complete (False only when
    # cancelled during the first layout: the rest is in unplaced), separate_colors.
    meta: dict = field(default_factory=dict)


def place(geom: BaseGeometry, rotation: float, x: float, y: float, mirror: bool = False) -> BaseGeometry:
    """THE transform for a Placement. Every module must use this.
    mirror flips x -> -x about (0,0) before the rotation (ring orientation is
    reversed by the flip; CAM re-orients rings itself)."""
    g = affinity.scale(geom, -1.0, 1.0, origin=(0, 0)) if mirror else geom
    g = affinity.rotate(g, rotation, origin=(0, 0), use_radians=False) if rotation else g
    return affinity.translate(g, x, y)


def placed_part(part: Part, pl: Placement) -> tuple[Polygon, list[LineString]]:
    m = bool(getattr(pl, "mirror", False))
    return (place(part.shape, pl.rotation, pl.x, pl.y, m),
            [place(g, pl.rotation, pl.x, pl.y, m) for g in part.marks])


__all__ = ["LAYERS", "Part", "SheetSpec", "NestSettings", "Placement", "SheetResult", "NestResult",
           "place", "placed_part", "LineString", "MultiLineString", "Polygon"]
