"""Ball-nose finishing and flat roughing over a height map (drop-cutter)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage


@dataclass
class Tool:
    name: str
    diameter_mm: float
    ball: bool = True
    feed_mm_min: float = 3000.0
    plunge_mm_min: float = 800.0
    rpm: int = 18000
    stepover_mm: float | None = None   # default: 10% of diameter (ball), 40% (flat)
    stepdown_mm: float = 2.0           # roughing only

    def __post_init__(self):
        if self.diameter_mm <= 0:
            raise ValueError("tool diameter must be > 0")
        if self.stepover_mm is None:
            self.stepover_mm = self.diameter_mm * (0.10 if self.ball else 0.40)
        if self.stepover_mm <= 0 or self.stepdown_mm <= 0:
            raise ValueError("stepover and stepdown must be > 0")


def _footprint(radius_px: int):
    yy, xx = np.mgrid[-radius_px:radius_px + 1, -radius_px:radius_px + 1]
    return xx, yy, (xx ** 2 + yy ** 2) <= radius_px ** 2


def tool_tip_surface(H: np.ndarray, px_mm: float, tool: Tool) -> np.ndarray:
    """Lowest Z the tool tip can reach at each pixel without gouging the relief."""
    r_mm = tool.diameter_mm / 2
    r_px = max(1, int(np.ceil(r_mm / px_mm)))
    xx, yy, inside = _footprint(r_px)
    if tool.ball:
        d = np.hypot(xx, yy) * px_mm
        # surface point at distance d touches the ball when tip is at H - (r - sqrt(r^2-d^2))
        offs = np.where(inside, np.sqrt(np.clip(r_mm ** 2 - d ** 2, 0, None)) - r_mm, -1e9)
    else:
        offs = np.where(inside, 0.0, -1e9)
    return ndimage.grey_dilation(H, structure=offs, mode="nearest")


def _simplify(xs: np.ndarray, zs: np.ndarray, tol: float = 2e-5):
    """Keep only the points where the Z profile bends (vectorised).

    A point is dropped when it sits on the straight line between its two
    neighbours (second difference below tol), so flat floors and bevels
    collapse to their end points while curves keep every sample.
    """
    if len(xs) < 3:
        return np.arange(len(xs))
    bend = np.abs(zs[:-2] - 2 * zs[1:-1] + zs[2:]) > tol
    keep = np.concatenate(([True], bend, [True]))
    return np.nonzero(keep)[0]


def raster(Z: np.ndarray, px_mm: float, stepover_mm: float, active: np.ndarray | None = None):
    """Zig-zag lines along X, coordinates in mm (X right, Y up, Z as given).

    active: optional bool mask; each row is split into runs where it is True,
    so the writer can lift over parts that need no cutting.
    Returns a list of polylines, each an (n, 3) array.
    """
    rows = np.arange(0, Z.shape[0], max(1, round(stepover_mm / px_mm)))
    if rows[-1] != Z.shape[0] - 1:
        rows = np.append(rows, Z.shape[0] - 1)
    xs = np.arange(Z.shape[1]) * px_mm
    lines = []
    for k, r in enumerate(rows):
        y = (Z.shape[0] - 1 - r) * px_mm
        on = np.ones(Z.shape[1], bool) if active is None else active[r]
        if not on.any():
            continue
        # start/end index of every run of True
        edges = np.diff(np.concatenate(([0], on.astype(np.int8), [0])))
        starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
        runs = []
        for a, b in zip(starts, ends):
            idx = a + _simplify(xs[a:b], Z[r, a:b])
            runs.append(np.column_stack((xs[idx], np.full(len(idx), y), Z[r, idx])))
        if k % 2:  # zig-zag: odd rows run right to left
            runs = [ln[::-1] for ln in reversed(runs)]
        lines.extend(runs)
    return lines


def finishing(H, px_mm, tool: Tool, top_z: float, skip: np.ndarray | None = None):
    """Finishing pass: tip surface shifted so the highest relief point = top_z.
    skip: pixels already finished by the flat floor pass (left out)."""
    tip = tool_tip_surface(H, px_mm, tool) - H.max() + top_z
    active = None
    if skip is not None:
        # overlap one stepover into the floor so there is no ridge at the seam
        grow = max(1, round(tool.stepover_mm / px_mm))
        active = ndimage.binary_dilation(~skip, iterations=grow)
    return raster(tip, px_mm, tool.stepover_mm, active=active)


def floor_mask(H, px_mm, tool: Tool, floor_h: float = 0.0) -> np.ndarray:
    """Pixels where this flat tool can sit on the background (height floor_h)
    without touching any raised part."""
    tip = tool_tip_surface(H, px_mm, tool)
    return tip <= floor_h + 1e-3


def roughing(H, px_mm, tool: Tool, top_z: float, allowance_mm: float = 0.5,
             floor_pass: bool = True):
    """Layered roughing, leaving allowance_mm of material for the finishing bit.

    At each level the tool runs at max(level, protected surface), so it never
    goes below the finished shape plus allowance. Only the parts of a row that
    still have material above the surface at this level are cut.
    With floor_pass, a flat tool then cleans the open background to its exact
    depth, so the ball bit only has to finish the raised shapes.
    Returns (list of levels, each a list of polylines; floor mask or None).
    """
    surf = tool_tip_surface(H, px_mm, tool) - H.max() + top_z + allowance_mm
    low = float(surf.min())
    passes = []
    prev = top_z
    level = top_z - tool.stepdown_mm
    while prev > low + 1e-6:
        lv = max(level, low)
        Z = np.maximum(surf, lv)
        active = surf < prev - 1e-6      # material between this level and the last
        if active.any():
            passes.append(raster(Z, px_mm, tool.stepover_mm, active=active))
        prev = lv
        level -= tool.stepdown_mm
    floor = None
    if floor_pass and not tool.ball:
        floor = floor_mask(H, px_mm, tool)
        if floor.any():
            z_floor = 0.0 - H.max() + top_z
            Zf = np.full(H.shape, z_floor)
            passes.append(raster(Zf, px_mm, tool.stepover_mm, active=floor))
        else:
            floor = None
    return passes, floor
