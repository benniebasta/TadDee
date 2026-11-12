"""True-shape nesting engine (PLAN.md section 4.4).

How it works, in plain words:

1. Every part copy becomes one "instance". Parts with qty 0 are skipped.
2. For each part and each allowed rotation we rotate the true shape, grow it
   by half the gap ("inflated" shape) and move it so its bounding box starts
   at (0, 0). Two inflated shapes that do not overlap are at least `gap`
   apart. The offset is remembered so the final Placement refers to the real
   (uninflated) part, using core.models.place().
3. The usable area of a sheet is its outline shrunk by (margin - gap/2): the
   inflated shape may reach gap/2 further than the real part, so a real part
   then keeps `margin` from the edge.
4. The sheet becomes a grid of square cells (resolution_mm). A cell is 1
   (blocked) when it is outside the usable area or covered by a part that is
   already placed. Shapes are drawn with Pillow.
5. Each inflated shape also becomes a small grid (its "kernel"), grown by one
   cell so grid rounding cannot hide a real overlap. Correlating the sheet
   grid with the kernel (FFT, scipy.signal.fftconvolve) tells, for every cell,
   whether the shape would hit something if its corner sat there. Among the
   free cells we take the one with the smallest right edge, then the smallest
   y (gravity to the left, then down), so the leftover is one strip at +X.
6. All rotations are tried; the best one wins. The winner is then checked
   with exact shapely geometry (inside the margin, gap kept to every placed
   part). If the check fails, that cell is crossed out and the next best cell
   is tried. A passing position is then slid a little further left and down
   with exact checks, to win back the space the grid rounding wasted.
7. Instances go biggest first. A sheet is filled as far as possible before
   the next one is opened. Sheets are used in the given order (offcuts are
   listed first by the caller); qty=None means as many copies as needed.
   Copies that fit no sheet end up in `unplaced`.
8. While time is left, other orders are tried (neighbour swaps, shuffles of
   parts of similar area) with a seeded random generator; the best layout
   wins. The first attempt is always plain biggest-first, so time_limit_s=0
   still gives a result. Same input + same seed = same output.
9. Each sheet reports used area, utilisation and a reusable offcut strip.

Production additions (PLAN.md Part 2):
- Part.mirror: the part is flipped (x -> -x) before rotating; the Placement
  carries mirror=True so CAM and previews draw it flipped too.
- settings.separate_colors: a sheet takes only parts of the colour of the
  first part placed on it (Part.color), so colours never share a sheet.
- Fine resolutions are coarsened so one sheet grid has at most
  settings.max_grid_cells cells (12 M: 0.5 mm on 2440 x 1220); the used
  resolution is reported in NestResult.meta.
- nest(..., progress=, should_stop=): progress(fraction 0..1, best summary)
  is called now and then; should_stop() is polled between rotations, so a
  cancel stops within one FFT (well under a second). The best finished layout
  is kept. A cancel during the very first layout returns what was placed so
  far, the rest as unplaced (meta["complete"] = False).
- Occupancy FFTs are shared by all rotations of one instance and kernel FFTs
  are cached (LRU, bounded bytes), so many rotations cost about one inverse
  FFT each.
"""
from __future__ import annotations

import math
import random
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import shapely
from PIL import Image, ImageDraw
from scipy import fft as sp_fft
from scipy.ndimage import binary_dilation
from scipy.signal import fftconvolve
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from core.models import NestResult, NestSettings, Part, Placement, SheetResult, SheetSpec, place

__all__ = ["nest", "validate", "placed_shape", "sheet_container", "effective_resolution"]

# Tolerances (mm). The exact check during nesting is a little stricter than
# validate(), so whatever the engine accepts always passes validate().
_TOL_VALIDATE = 0.05
_QUAD_SEGS = 32                 # segments per quarter circle of the gap/2 buffer


def _engine_tol(gap: float) -> float:
    """Tolerance of the engine's exact check: the buffer's round corners are
    polygons whose flat edges cut inside the true circle by at most
    (gap/2) * (1 - cos(half a step)); add 0.001 mm for float noise. With
    32 segments this is about 0.002 mm for a 6 mm gap, far below validate()'s 0.05."""
    return (gap / 2) * (1 - math.cos(math.pi / (4 * _QUAD_SEGS))) + 0.001
_MAX_EXACT_FAILS = 400          # per instance per sheet, then the sheet counts as full for it
_STALE_ORDERS_STOP = 60         # stop improving after this many repeated orders in a row
_KFFT_BUDGET = 256 * 1024 * 1024   # bytes of cached kernel FFTs
_WIN_STEP_MM = 120.0            # search windows grow in steps of this many mm (kernel FFT cache reuse)
_PROGRESS_EVERY_S = 0.25
_PRUNE_CELL_MM = 2.0            # coarse cell used to rank many rotations on a fine grid
_PRUNE_KEEP = 4                 # rotations searched on the fine grid after ranking


class _Stopped(Exception):
    """should_stop() said yes in the middle of an attempt."""


def effective_resolution(requested: float, sheets: list[SheetSpec], max_cells: int) -> float:
    """The cell size the engine really uses: the requested one, made coarser
    (in 0.05 mm steps) when the biggest sheet would need more than max_cells."""
    r = float(requested) if requested and requested > 0 else 2.0
    if max_cells and max_cells > 0 and sheets:
        area = max((s.polygon().bounds[2] - s.polygon().bounds[0]) * (s.polygon().bounds[3] - s.polygon().bounds[1])
                   for s in sheets)
        need = math.sqrt(area / max_cells) if area > 0 else 0.0
        if need > r:
            r = math.ceil(need / 0.05 - 1e-9) * 0.05
    return round(r, 4)


# --------------------------------------------------------------------------- helpers others may use

def placed_shape(part: Part, pl: Placement) -> Polygon:
    """The real part polygon at its placement (thin wrapper over core.models.place)."""
    return place(part.shape, pl.rotation, pl.x, pl.y, bool(getattr(pl, "mirror", False)))


def sheet_container(spec: SheetSpec) -> BaseGeometry:
    """Where real parts may lie on a sheet: the sheet outline shrunk by its margin."""
    return spec.polygon().buffer(-spec.margin, join_style="mitre")


# --------------------------------------------------------------------------- rasterising

def _polys(geom: BaseGeometry) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return sorted(geom.geoms, key=lambda g: -g.area)
    return sorted((g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)), key=lambda g: -g.area)


def _raster(geom: BaseGeometry, x0: float, y0: float, r: float, nx: int, ny: int,
            holes: bool = True) -> np.ndarray:
    """Boolean grid (ny rows, nx cols). Cell (i, j) spans x0+j*r .. x0+(j+1)*r and
    y0+i*r .. y0+(i+1)*r (row 0 at the bottom, Y up). Pillow marks the cells the
    shape covers, including most boundary cells."""
    img = Image.new("L", (max(nx, 1), max(ny, 1)), 0)
    draw = ImageDraw.Draw(img)

    def pts(ring):
        c = np.asarray(ring.coords)
        return [((x - x0) / r - 0.5, (y - y0) / r - 0.5) for x, y in c]

    for poly in _polys(geom):
        draw.polygon(pts(poly.exterior), fill=1, outline=1)
        if holes:
            for interior in poly.interiors:
                draw.polygon(pts(interior), fill=0, outline=0)
    return np.asarray(img, dtype=bool)[:ny, :nx]


# --------------------------------------------------------------------------- per (part, rotation) data

@dataclass
class _Shape:
    part_id: str
    rotation: float
    mirror: bool
    order: int                  # index of the rotation in settings.rotations (tie-break)
    bx: float                   # bounds of the rotated, inflated shape before normalising
    by: float
    w: float                    # inflated width / height
    h: float
    inflated: Polygon           # normalised: bounds start at (0, 0)
    kernel: np.ndarray          # float, (kh, kw), flipped for fftconvolve, grown by 1 cell
    kh: int
    kw: int
    ncells: int


class _Context:
    """Everything that stays the same for one nest() call: kernels, sheet grids."""

    def __init__(self, parts: dict[str, Part], specs: list[SheetSpec], st: NestSettings,
                 should_stop: Callable[[], bool] | None = None):
        self.parts = parts
        self.specs = specs
        self.gap = max(0.0, float(st.gap_mm))
        self.r_requested = float(st.resolution_mm) if st.resolution_mm and st.resolution_mm > 0 else 2.0
        self.r = effective_resolution(self.r_requested, specs, int(getattr(st, "max_grid_cells", 0) or 0))
        self.separate = bool(getattr(st, "separate_colors", False))
        self.should_stop = should_stop
        self.deadline = math.inf            # perf_counter time after which later attempts give up
        self._kfft: OrderedDict = OrderedDict()
        self._kfft_bytes = 0
        # coarse pre-ranking of rotations: pool fine cells into ~2 mm cells
        self.pool_f = max(1, int(round(_PRUNE_CELL_MM / self.r)))
        self._coarse: dict[tuple[str, int], np.ndarray] = {}

        self.holes = bool(st.allow_in_holes)
        self.tol = _engine_tol(self.gap)
        rots: list[float] = []
        for a in (st.rotations or [0.0]):
            a = float(a) % 360.0
            if not any(abs(a - b) < 1e-9 for b in rots):
                rots.append(a)
        self.rotations = rots
        self._shapes: dict[str, list[_Shape]] = {}
        self._bases: dict[int, _SheetBase | None] = {}
        self.never_fits: set[tuple[str, int]] = set()   # (part id, spec index): does not fit even an empty sheet

    def coarse_kernel(self, s: "_Shape") -> np.ndarray:
        key = (s.part_id, s.order)
        k = self._coarse.get(key)
        if k is None:
            raw = s.kernel[::-1, ::-1] > 0.5          # back to the unflipped kernel
            k = np.ascontiguousarray(_pool(raw, self.pool_f, False)[::-1, ::-1], dtype=np.float32)
            self._coarse[key] = k
        return k

    def color(self, pid: str) -> str:
        return (self.parts[pid].color or "").lower()

    def check_stop(self, first: bool) -> None:
        """Raise _Stopped on cancel (any attempt) or, after the first attempt, on time."""
        if self.should_stop is not None and self.should_stop():
            raise _Stopped("cancel")
        if not first and time.perf_counter() > self.deadline:
            raise _Stopped("time")

    def kernel_fft(self, key: tuple, kernel: np.ndarray, fshape: tuple[int, int]) -> np.ndarray:
        key = key + (fshape,)
        f = self._kfft.get(key)
        if f is not None:
            self._kfft.move_to_end(key)
            return f
        with sp_fft.set_workers(-1):
            f = sp_fft.rfft2(kernel, s=fshape)
        self._kfft[key] = f
        self._kfft_bytes += f.nbytes
        while self._kfft_bytes > _KFFT_BUDGET and len(self._kfft) > 1:
            _, old = self._kfft.popitem(last=False)
            self._kfft_bytes -= old.nbytes
        return f

    # Kernels are cached per (part id, rotation). Rotations that give the same
    # inflated shape (a rectangle at 0 and 180) are tried only once.
    def shapes(self, pid: str) -> list[_Shape]:
        if pid in self._shapes:
            return self._shapes[pid]
        part = self.parts[pid]
        mirror = bool(part.mirror)
        out: list[_Shape] = []
        r = self.r
        for k, rot in enumerate(self.rotations):
            rotated = place(part.shape, rot, 0, 0, mirror)
            infl = (rotated.buffer(self.gap / 2, join_style="round", quad_segs=_QUAD_SEGS)
                    if self.gap > 0 else rotated)
            if not isinstance(infl, Polygon):     # buffer of one valid polygon is one polygon; be safe
                infl = max(_polys(infl), key=lambda g: g.area)
            bx, by, mx, my = infl.bounds
            norm = affinity.translate(infl, -bx, -by)
            w, h = mx - bx, my - by
            if any(abs(s.w - w) < 0.5 and abs(s.h - h) < 0.5 and s.inflated.symmetric_difference(norm).area < 0.5
                   for s in out):
                continue
            cw, ch = int(math.ceil(w / r - 1e-9)) + 2, int(math.ceil(h / r - 1e-9)) + 2
            # Bigger than every sheet grid: it can never be placed, so do not draw it
            # (a drawing read in the wrong units can be tens of metres: GBs of grid).
            if not any(b is not None and ch <= b.ny and cw <= b.nx for b in self.all_bases()):
                continue
            # draw with a one-cell border (shape origin at cell 1,1), then grow by one cell
            raw = _raster(norm, -r, -r, r, cw, ch, holes=self.holes)
            ker = binary_dilation(raw, structure=np.ones((3, 3), bool))
            ncells = int(ker.sum())
            dtype = np.float32 if ncells < 200_000 else np.float64
            out.append(_Shape(pid, rot, mirror, k, bx, by, w, h, norm, np.ascontiguousarray(ker[::-1, ::-1], dtype=dtype),
                              ch, cw, ncells))
        self._shapes[pid] = out
        return out

    def all_bases(self) -> list["_SheetBase | None"]:
        return [self.base(si) for si in range(len(self.specs))]

    def base(self, si: int) -> "_SheetBase | None":
        if si not in self._bases:
            self._bases[si] = _SheetBase.build(self.specs[si], self.gap, self.r)
        return self._bases[si]


@dataclass
class _SheetBase:
    """The empty grid of one sheet spec, reused for every copy and attempt."""
    spec: SheetSpec
    usable: BaseGeometry        # area the inflated shapes must stay inside
    container: BaseGeometry     # area the real parts must stay inside (engine tolerance applied)
    ox: float
    oy: float
    nx: int
    ny: int
    occ: np.ndarray             # bool, True = blocked
    free0: int

    @staticmethod
    def build(spec: SheetSpec, gap: float, r: float) -> "_SheetBase | None":
        poly = spec.polygon()
        if poly.is_empty or poly.area <= 0:
            return None
        # (the inflated shapes reach at least gap/2 beyond the part everywhere, so
        # keeping them inside this area keeps every real part `margin` from the edge)
        usable = poly.buffer(-spec.margin + gap / 2, join_style="mitre")
        if usable.is_empty or usable.area <= 0:
            return None
        minx, miny, maxx, maxy = usable.bounds
        # One spare cell on every side: the kernel's extra ring may hang over the edge.
        ox, oy = minx - r, miny - r
        nx = int(math.ceil((maxx - minx) / r - 1e-9)) + 2
        ny = int(math.ceil((maxy - miny) / r - 1e-9)) + 2
        # A cell is free when it lies within one cell of the usable area; the
        # kernel ring (one cell) may sit there while the shape itself stays inside.
        grown = usable.buffer(r * 0.999, join_style="mitre")
        occ = ~_raster(grown, ox, oy, r, nx, ny, holes=True)
        container = poly.buffer(-spec.margin + _engine_tol(gap), join_style="mitre")
        shapely.prepare(container)
        return _SheetBase(spec, usable, container, ox, oy, nx, ny, occ, int((~occ).sum()))


class _Grid:
    """One sheet copy being filled."""

    def __init__(self, base: _SheetBase, si: int):
        self.base = base
        self.si = si
        self.occ = base.occ.copy()
        self.free = base.free0
        self.max_col = 0                        # rightmost column touched by a placed part
        self.max_x = -math.inf                  # rightmost x of placed inflated shapes (mm)
        self.items: list[tuple[str, int, _Shape, float, float]] = []   # pid, copy, shape, X, Y (inflated origin)
        self.true: list[Polygon] = []
        self.bounds = np.zeros((0, 4))
        self.color: str | None = None           # with separate_colors: the colour this sheet takes

    @property
    def empty(self) -> bool:
        return not self.items


# --------------------------------------------------------------------------- placing one instance

def _best_cell(free: np.ndarray) -> tuple[int, int] | None:
    """Free cell with the smallest column, then the smallest row."""
    cols = free.any(axis=0)
    if not cols.any():
        return None
    j = int(np.argmax(cols))
    i = int(np.argmax(free[:, j]))
    return i, j


def _free_map(grid: _Grid, s: _Shape, full: bool) -> tuple[np.ndarray, bool]:
    """Cells where the kernel's corner may sit without touching anything blocked.
    Unless `full`, only the columns up to the placed parts' right end plus the
    kernel width are searched: any position further right has a larger right
    edge than one found there. Returns (free map, searched everything)."""
    nx = grid.base.nx
    wc = nx if full else min(nx, grid.max_col + s.kw + 2)
    occ = grid.occ[:, :wc].astype(s.kernel.dtype)
    with sp_fft.set_workers(-1):
        coll = fftconvolve(occ, s.kernel, mode="valid")
    return coll < 0.5, wc >= nx


def _batch_valid(ctx: _Context, occ: np.ndarray, kernels: list[np.ndarray], keys: list, first: bool,
                 dtype) -> list[np.ndarray]:
    """'valid' correlations of one occupancy window with several (flipped)
    kernels: the window is transformed once, kernel transforms come from the
    LRU cache, each kernel then costs one inverse FFT."""
    ny, wc = occ.shape
    kh = max(k.shape[0] for k in kernels)
    kw = max(k.shape[1] for k in kernels)
    fshape = (sp_fft.next_fast_len(ny + kh - 1, real=True), sp_fft.next_fast_len(wc + kw - 1, real=True))
    ctx.check_stop(first)
    with sp_fft.set_workers(-1):
        focc = sp_fft.rfft2(occ.astype(dtype), s=fshape)
    out = []
    for ker, key in zip(kernels, keys):
        ctx.check_stop(first)
        kf = ctx.kernel_fft(key, ker, fshape)
        with sp_fft.set_workers(-1):
            conv = sp_fft.irfft2(focc * kf, s=fshape)
        out.append(conv[ker.shape[0] - 1:ny, ker.shape[1] - 1:wc])
    return out


def _free_maps(grid: _Grid, ctx: _Context, shapes: list[_Shape], first: bool) -> list[tuple[np.ndarray, bool]]:
    """_free_map for several kernels at once (one shared occupancy FFT).
    Polls ctx.check_stop() between rotations."""
    if len(shapes) == 1:
        ctx.check_stop(first)
        return [_free_map(grid, shapes[0], full=False)]
    nx = grid.base.nx
    kw = max(s.kw for s in shapes)
    step = max(16, int(round(_WIN_STEP_MM / ctx.r)))
    wc = min(nx, int(math.ceil((grid.max_col + kw + 2) / step)) * step)
    dtype = np.float64 if any(s.kernel.dtype == np.float64 for s in shapes) else np.float32
    colls = _batch_valid(ctx, grid.occ[:, :wc], [s.kernel.astype(dtype, copy=False) for s in shapes],
                         [("f", s.part_id, s.order, np.dtype(dtype).str) for s in shapes], first, dtype)
    return [(c < 0.5, wc >= nx) for c in colls]


def _pool(a: np.ndarray, f: int, pad_value: bool) -> np.ndarray:
    """Coarse grid: a coarse cell is True when any of its f x f fine cells is."""
    ny, nx = a.shape
    py, px = (-ny) % f, (-nx) % f
    if py or px:
        a = np.pad(a, ((0, py), (0, px)), constant_values=pad_value)
    return a.reshape(a.shape[0] // f, f, a.shape[1] // f, f).any(axis=(1, 3))


def _prune_rotations(grid: _Grid, ctx: _Context, shapes: list[_Shape], first: bool) -> list[_Shape]:
    """With many rotations on a fine grid, rank them on a coarse copy of the grid
    (about 2 mm cells, conservative: a coarse free cell is free on the fine
    grid too) and keep the _PRUNE_KEEP best for the fine search. If none fits
    on the coarse grid (a tight spot), all rotations are searched fine."""
    f = ctx.pool_f
    occ_c = _pool(grid.occ, f, True)
    nxc = occ_c.shape[1]
    kers, keys = [], []
    for s in shapes:
        kc = ctx.coarse_kernel(s)
        kers.append(kc)
        keys.append(("c", s.part_id, s.order))
    kwc = max(k.shape[1] for k in kers)
    if any(k.shape[0] > occ_c.shape[0] or k.shape[1] > nxc for k in kers):
        return shapes
    step = max(16, int(round(_WIN_STEP_MM / (ctx.r * f))))
    wc = min(nxc, int(math.ceil((int(math.ceil(grid.max_col / f)) + kwc + 2) / step)) * step)
    colls = _batch_valid(ctx, occ_c[:, :wc], kers, keys, first, np.float32)
    base, r = grid.base, ctx.r
    ranked = []
    for s, c in zip(shapes, colls):
        cell = _best_cell(c < 0.5)
        if cell is not None:
            i, j = cell
            ranked.append(((base.ox + (f * j + 1) * r + s.w, base.oy + (f * i + 1) * r, s.order), s))
    if not ranked:
        return shapes
    ranked.sort(key=lambda t: t[0])
    keep = {id(s) for _, s in ranked[:_PRUNE_KEEP]}
    return [s for s in shapes if id(s) in keep]


def _exact_ok(grid: _Grid, ctx: _Context, poly: Polygon) -> bool:
    """Exact shapely check: inside the margin and at least gap from every placed part."""
    if not grid.base.container.covers(poly):
        return False
    if not grid.true:
        return True
    g = ctx.gap
    b = poly.bounds
    bb = grid.bounds
    m = (bb[:, 0] < b[2] + g) & (bb[:, 2] > b[0] - g) & (bb[:, 1] < b[3] + g) & (bb[:, 3] > b[1] - g)
    idx = np.nonzero(m)[0]
    if idx.size == 0:
        return True
    others = [grid.true[k] for k in idx]
    tol = ctx.tol
    if g > tol + 1e-6:
        return not bool(np.any(shapely.dwithin(others, poly, g - tol)))
    return all(o.intersection(poly).area <= 0.01 for o in others)


def _slide(grid: _Grid, ctx: _Context, part: Part, s: _Shape, X: float, Y: float) -> tuple[float, float]:
    """Push the accepted position further left, then down, then left again, by up
    to 2.5 cells using bisection with the exact check (wins back raster slack)."""
    span = 2.5 * ctx.r

    def ok(x, y):
        return _exact_ok(grid, ctx, place(part.shape, s.rotation, x - s.bx, y - s.by, s.mirror))

    for axis in (0, 1, 0):
        lo, hi = 0.0, span
        if ok(X - hi, Y) if axis == 0 else ok(X, Y - hi):
            lo = hi
        else:
            for _ in range(7):
                mid = (lo + hi) / 2
                if ok(X - mid, Y) if axis == 0 else ok(X, Y - mid):
                    lo = mid
                else:
                    hi = mid
        if axis == 0:
            X -= lo
        else:
            Y -= lo
    return X, Y


def _place_one(grid: _Grid, ctx: _Context, pid: str, copy: int, first: bool = True) -> bool:
    part = ctx.parts[pid]
    base = grid.base
    r = ctx.r
    cands = []   # [free map, searched all columns, shape, best cell, crossed-out cells]
    shapes = [s for s in ctx.shapes(pid) if not (s.kh > base.ny or s.kw > base.nx or s.ncells > grid.free)]
    if len(shapes) > _PRUNE_KEEP and ctx.pool_f >= 2:
        shapes = _prune_rotations(grid, ctx, shapes, first)
    maps = _free_maps(grid, ctx, shapes, first) if shapes else []
    for s, (free, full) in zip(shapes, maps):
        cell = _best_cell(free)
        if cell is None and not full:
            ctx.check_stop(first)
            free, full = _free_map(grid, s, full=True)
            cell = _best_cell(free)
        if cell is not None:
            cands.append([free, full, s, cell, []])

    def key(c):
        i, j = c[3]
        return (base.ox + (j + 1) * r + c[2].w, base.oy + (i + 1) * r, c[2].order)

    fails = 0
    while cands and fails <= _MAX_EXACT_FAILS:
        c = min(cands, key=key)
        free, full, s, (i, j), crossed = c
        X, Y = base.ox + (j + 1) * r, base.oy + (i + 1) * r
        poly = place(part.shape, s.rotation, X - s.bx, Y - s.by, s.mirror)
        if _exact_ok(grid, ctx, poly):
            X2, Y2 = _slide(grid, ctx, part, s, X, Y)
            X2, Y2 = round(X2, 4), round(Y2, 4)
            poly2 = place(part.shape, s.rotation, X2 - s.bx, Y2 - s.by, s.mirror)
            if _exact_ok(grid, ctx, poly2):
                X, Y, poly = X2, Y2, poly2
            _commit(grid, ctx, pid, copy, s, X, Y, poly)
            return True
        # Exact check failed (rare): cross out the cell and take the next best one.
        fails += 1
        free[i, j] = False
        crossed.append((i, j))
        cell = _best_cell(free)
        if cell is None and not full:
            ctx.check_stop(first)
            free, full = _free_map(grid, s, full=True)
            for ci, cj in crossed:
                free[ci, cj] = False
            cell = _best_cell(free)
        if cell is None:
            cands.remove(c)
        else:
            c[0], c[1], c[3] = free, full, cell
    return False


def _commit(grid: _Grid, ctx: _Context, pid: str, copy: int, s: _Shape, X: float, Y: float, poly: Polygon) -> None:
    """Record the placement and mark its inflated shape as blocked on the grid."""
    base, r = grid.base, ctx.r
    infl = affinity.translate(s.inflated, X, Y)
    minx, miny, maxx, maxy = infl.bounds
    j0 = max(0, int(math.floor((minx - base.ox) / r)) - 1)
    i0 = max(0, int(math.floor((miny - base.oy) / r)) - 1)
    j1 = min(base.nx, int(math.ceil((maxx - base.ox) / r)) + 1)
    i1 = min(base.ny, int(math.ceil((maxy - base.oy) / r)) + 1)
    if j1 > j0 and i1 > i0:
        stamp = _raster(infl, base.ox + j0 * r, base.oy + i0 * r, r, j1 - j0, i1 - i0, holes=ctx.holes)
        win = grid.occ[i0:i1, j0:j1]
        grid.free -= int((stamp & ~win).sum())
        win |= stamp
        grid.max_col = max(grid.max_col, j1)
    grid.max_x = max(grid.max_x, maxx)
    grid.items.append((pid, copy, s, X, Y))
    grid.true.append(poly)
    grid.bounds = np.vstack([grid.bounds, np.asarray(poly.bounds)[None, :]])


# --------------------------------------------------------------------------- one full attempt

@dataclass
class _Layout:
    grids: list[_Grid]
    unplaced: Counter
    score: tuple = field(default=())
    complete: bool = True


def _run(ctx: _Context, order: list[tuple[str, int]], first: bool = False,
         on_step: Callable[[float], None] | None = None) -> _Layout | None:
    """One attempt for one order. Later attempts (first=False) return None when
    ctx.deadline passes or should_stop() says yes. The first attempt ignores the
    deadline; on cancel it returns what it placed so far (complete=False)."""
    remaining = list(order)
    grids: list[_Grid] = []
    total = max(1, len(order))
    done = 0
    try:
        for si, spec in enumerate(ctx.specs):
            if not remaining:
                break
            base = ctx.base(si)
            if base is None:
                continue
            limit = math.inf if spec.qty is None else max(0, int(spec.qty))
            used = 0
            while remaining and used < limit:
                grid = _Grid(base, si)
                failed: set[str] = set()       # parts that did not fit this sheet copy: their other copies won't either
                left: list[tuple[str, int]] = []
                for n, (pid, copy) in enumerate(remaining):
                    ctx.check_stop(first)
                    if pid in failed or (pid, si) in ctx.never_fits:
                        left.append((pid, copy))
                        continue
                    if ctx.separate and grid.color is not None and ctx.color(pid) != grid.color:
                        left.append((pid, copy))   # another colour: waits for its own sheet
                        continue
                    was_empty = grid.empty
                    try:
                        ok = _place_one(grid, ctx, pid, copy, first)
                    except _Stopped:
                        left.extend(remaining[n:])
                        remaining = left
                        if not grid.empty:
                            grids.append(grid)
                        raise
                    if ok:
                        if grid.color is None:
                            grid.color = ctx.color(pid)
                        done += 1
                        if on_step is not None:
                            on_step(done / total)
                        continue
                    failed.add(pid)
                    if was_empty and not (ctx.separate and grid.color is not None):
                        ctx.never_fits.add((pid, si))
                    left.append((pid, copy))
                remaining = left
                if grid.empty:
                    break                       # nothing more fits this kind of sheet
                grids.append(grid)
                used += 1
    except _Stopped:
        if not first:
            return None
        lay = _Layout(grids, Counter(pid for pid, _ in remaining), complete=False)
        lay.score = _score(ctx, lay)
        return lay
    lay = _Layout(grids, Counter(pid for pid, _ in remaining))
    lay.score = _score(ctx, lay)
    return lay


def _score(ctx: _Context, lay: _Layout) -> tuple:
    """Smaller is better: fewer unplaced copies, fewer sheets, shorter used
    length on the last sheet, higher utilisation."""
    unplaced_area = sum(ctx.parts[p].area * n for p, n in lay.unplaced.items())
    last = lay.grids[-1].max_x - lay.grids[-1].base.ox if lay.grids else 0.0
    used = sum(ctx.parts[p].area for g in lay.grids for p, *_ in g.items)
    total = sum(g.base.spec.polygon().area for g in lay.grids) or 1.0
    return (sum(lay.unplaced.values()), round(unplaced_area, 3), len(lay.grids), round(last, 3), -round(used / total, 9))


# --------------------------------------------------------------------------- orders for the improvement loop

def _perturb(order: list[tuple[str, int]], areas: dict[str, float], rng: random.Random) -> list[tuple[str, int]]:
    o = list(order)
    n = len(o)
    if n < 2:
        return o
    if rng.random() < 0.5:
        # swap a few neighbours (only swaps of different parts change anything)
        for _ in range(rng.randint(1, max(1, n // 10))):
            k = rng.randrange(n - 1)
            o[k], o[k + 1] = o[k + 1], o[k]
    else:
        # shuffle runs of parts whose area is within 15 % of the run's first part
        out, k = [], 0
        while k < n:
            a0 = areas[o[k][0]]
            m = k + 1
            while m < n and abs(areas[o[m][0]] - a0) <= 0.15 * max(a0, 1e-9):
                m += 1
            run = o[k:m]
            rng.shuffle(run)
            out.extend(run)
            k = m
        o = out
        # sometimes also move one random instance a few places earlier
        if rng.random() < 0.3:
            k = rng.randrange(1, n)
            t = max(0, k - rng.randint(1, 5))
            o.insert(t, o.pop(k))
    # keep copy numbers in order per part so identical layouts compare equal
    nxt: dict[str, int] = {}
    fixed = []
    for pid, _ in o:
        c = nxt.get(pid, 0)
        nxt[pid] = c + 1
        fixed.append((pid, c))
    return fixed


# --------------------------------------------------------------------------- public API

def nest(parts: list[Part], sheets: list[SheetSpec], settings: NestSettings, *,
         progress: Callable[[float, dict], None] | None = None,
         should_stop: Callable[[], bool] | None = None) -> NestResult:
    """Pack all part copies on as few sheets as possible. See module docstring.

    progress(fraction, best) is called about 4 times a second and once at the
    end with 1.0; best = {"attempts", "sheets", "unplaced", "utilisation",
    "elapsed_s"} of the best layout so far (None before the first one is done).
    should_stop() is polled often; when it returns True the run stops and the
    best layout so far is returned (meta["stopped"] == "cancel")."""
    t0 = time.perf_counter()
    by_id: dict[str, Part] = {}
    for p in parts:
        by_id.setdefault(p.id, p)
    ctx = _Context(by_id, list(sheets), settings, should_stop=should_stop)
    limit = max(0.0, float(settings.time_limit_s or 0.0))

    # Step 1 + 7: instances, biggest first (ties: input order, then copy number).
    # With separate colours, colour groups go one after the other (biggest group first).
    index = {p.id: k for k, p in enumerate(by_id.values())}
    instances = [(p.id, c) for p in by_id.values() if p.qty and p.qty > 0 for c in range(int(p.qty))]
    areas = {pid: by_id[pid].area for pid in by_id}
    group_key: dict[str, float] = {}
    if ctx.separate:
        tot: Counter = Counter()
        for pid, _ in instances:
            tot[ctx.color(pid)] += areas[pid]
        rank = {c: k for k, (c, _) in enumerate(sorted(tot.items(), key=lambda kv: (-kv[1], kv[0])))}
        group_key = {pid: rank[ctx.color(pid)] for pid in by_id if ctx.color(pid) in rank}
    order = sorted(instances, key=lambda t: (group_key.get(t[0], 0), -areas[t[0]], index[t[0]], t[1]))

    state = {"p": 0.0, "last": 0.0, "best": None}

    def summary(lay: _Layout, attempts: int) -> dict:
        used = sum(ctx.parts[pid].area for g in lay.grids for pid, *_ in g.items)
        area = sum(g.base.spec.polygon().area for g in lay.grids) or 1.0
        return dict(attempts=attempts, sheets=len(lay.grids), unplaced=int(sum(lay.unplaced.values())),
                    utilisation=round(used / area, 4), elapsed_s=round(time.perf_counter() - t0, 2))

    def report(frac: float, force: bool = False) -> None:
        if progress is None:
            return
        now = time.perf_counter()
        state["p"] = max(state["p"], min(1.0, frac))
        if force or now - state["last"] >= _PROGRESS_EVERY_S:
            state["last"] = now
            try:
                progress(state["p"], state["best"])
            except Exception:       # a broken callback must not kill the nest
                pass

    # the first layout is roughly a fifth of a timed run (all of an untimed one)
    w_first = 0.2 if limit > 0 else 0.99
    best = _run(ctx, order, first=True, on_step=lambda f: report(f * w_first))
    attempts = 1
    stopped = "done" if best.complete else "cancel"
    state["best"] = summary(best, attempts)
    report(w_first, force=True)

    # Step 8: seeded improvement loop until the time limit.
    rng = random.Random(settings.seed)
    seen = {tuple(pid for pid, _ in order)}
    stale = 0
    cur = order
    ctx.deadline = t0 + limit
    while best.complete and len(set(p for p, _ in order)) > 1:
        if time.perf_counter() - t0 >= limit:
            stopped = "time" if limit > 0 else "done"
            break
        if should_stop is not None and should_stop():
            stopped = "cancel"
            break
        cand = _perturb(cur if rng.random() < 0.7 else order, areas, rng)
        sig = tuple(pid for pid, _ in cand)
        if sig in seen:
            stale += 1
            if stale >= _STALE_ORDERS_STOP:
                stopped = "exhausted"
                break
            continue
        stale = 0
        seen.add(sig)
        lay = _run(ctx, cand)
        if lay is None:            # ran out of time or cancelled half-way: drop the unfinished attempt
            stopped = "cancel" if (should_stop is not None and should_stop()) else "time"
            break
        attempts += 1
        if lay.score < best.score:
            best, cur = lay, cand
            state["best"] = summary(best, attempts)
        report(w_first + (1 - w_first) * (time.perf_counter() - t0) / limit if limit > 0 else w_first)

    res = _build_result(ctx, best, settings, attempts, time.perf_counter() - t0)
    res.meta = dict(resolution_mm=ctx.r, resolution_requested=ctx.r_requested, rotations=list(ctx.rotations),
                    stopped=stopped, complete=best.complete, separate_colors=ctx.separate)
    state["best"] = summary(best, attempts)
    report(1.0, force=True)
    return res


def _build_result(ctx: _Context, lay: _Layout, st: NestSettings, attempts: int, elapsed: float) -> NestResult:
    out: list[SheetResult] = []
    used_total = area_total = 0.0
    for k, g in enumerate(lay.grids):
        spec = g.base.spec
        pls = []
        used = 0.0
        for pid, copy, s, X, Y in g.items:
            pls.append(Placement(part_id=pid, copy=copy, x=round(X - s.bx, 6), y=round(Y - s.by, 6),
                                 rotation=s.rotation, mirror=s.mirror))
            used += ctx.parts[pid].area
        sheet_area = spec.polygon().area
        out.append(SheetResult(sheet_id=spec.id, index=k, width=spec.width, height=spec.height,
                               placements=pls, used_area=used,
                               utilisation=used / sheet_area if sheet_area > 0 else 0.0,
                               offcut=_offcut(g, ctx, st), is_offcut=spec.is_offcut))
        used_total += used
        area_total += sheet_area
    return NestResult(sheets=out, unplaced={p: n for p, n in sorted(lay.unplaced.items()) if n > 0},
                      utilisation=used_total / area_total if area_total > 0 else 0.0,
                      elapsed_s=elapsed, attempts=attempts)


def _offcut(g: _Grid, ctx: _Context, st: NestSettings) -> Polygon | None:
    """Leftover strip at the +X end: from the placed parts' right end (+ gap/2)
    to the usable right end, full usable height, if both sides >= min_offcut_mm."""
    if not g.items:
        return None
    spec = g.base.spec
    usable = sheet_container(spec)
    if usable.is_empty:
        return None
    minx, miny, maxx, maxy = usable.bounds
    x0 = g.max_x + ctx.gap / 2
    if maxx - x0 < st.min_offcut_mm:
        return None
    region = box(x0, miny, maxx, maxy).intersection(usable)
    polys = _polys(region)
    if not polys:
        return None
    best = polys[0]
    bx0, by0, bx1, by1 = best.bounds
    if bx1 - bx0 >= st.min_offcut_mm and by1 - by0 >= st.min_offcut_mm:
        return best
    return None


def validate(result: NestResult, parts: list[Part], sheets: list[SheetSpec], settings: NestSettings) -> list[str]:
    """Check a result with exact geometry. [] means valid; otherwise one plain
    sentence per problem."""
    problems: list[str] = []
    by_id = {p.id: p for p in parts}
    specs = {s.id: s for s in sheets}
    gap = max(0.0, float(settings.gap_mm))
    counts: Counter = Counter()
    copies_seen: set[tuple[str, int]] = set()
    sheet_uses: Counter = Counter()

    for k, sh in enumerate(result.sheets):
        if sh.index != k:
            problems.append(f"Sheet {k + 1} has index {sh.index}; sheet indexes must be 0, 1, 2, ... in order.")
        spec = specs.get(sh.sheet_id)
        if spec is None:
            problems.append(f"Sheet {k + 1} refers to unknown sheet '{sh.sheet_id}'.")
            continue
        sheet_uses[spec.id] += 1
        inside = sheet_container(spec).buffer(_TOL_VALIDATE, join_style="mitre")
        shapely.prepare(inside)
        geoms: list[Polygon] = []
        labels: list[str] = []
        for pl in sh.placements:
            part = by_id.get(pl.part_id)
            if part is None:
                problems.append(f"Sheet {k + 1}: placement of unknown part '{pl.part_id}'.")
                continue
            name = f"{part.name or part.id} #{pl.copy + 1}"
            counts[pl.part_id] += 1
            if (pl.part_id, pl.copy) in copies_seen:
                problems.append(f"{name} is placed more than once.")
            copies_seen.add((pl.part_id, pl.copy))
            g = placed_shape(part, pl)
            if not inside.covers(g):
                problems.append(f"Sheet {k + 1}: {name} is outside the sheet or closer than "
                                f"{spec.margin:g} mm to its edge.")
            geoms.append(g)
            labels.append(name)
        if len(geoms) > 1:
            tree = STRtree(geoms)
            dmin = gap - _TOL_VALIDATE
            if dmin > 0:
                pairs = tree.query(geoms, predicate="dwithin", distance=dmin)
            else:
                pairs = tree.query(geoms, predicate="intersects")
            for a, b in zip(*pairs):
                if a >= b:
                    continue
                if dmin > 0:
                    d = geoms[a].distance(geoms[b])
                    what = "overlap" if geoms[a].intersection(geoms[b]).area > 0.01 else f"are only {d:.2f} mm apart"
                    problems.append(f"Sheet {k + 1}: {labels[a]} and {labels[b]} {what} "
                                    f"(gap must be {gap:g} mm).")
                elif geoms[a].intersection(geoms[b]).area > 0.01:
                    problems.append(f"Sheet {k + 1}: {labels[a]} and {labels[b]} overlap.")

    for pid, n in counts.items():
        part = by_id.get(pid)
        if part is not None and n > part.qty:
            problems.append(f"{part.name or pid} is placed {n} times but only {part.qty} are needed.")
    for sid, n in sheet_uses.items():
        q = specs[sid].qty
        if q is not None and n > q:
            problems.append(f"Sheet '{specs[sid].name or sid}' is used {n} times but only {q} are available.")
    return problems
