"""Write 2D toolpaths as G-code for NC Studio (router, no tool changer).

Same dialect as relief/gcode.py: one file per bit, the operator loads the
bit, sets Z0 on top of the material and runs the file. Units mm, absolute
coordinates, origin at the bottom-left corner of the sheet. CRLF line endings
because NC Studio runs on Windows. No M06 and no T words, ever.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .toolpaths import CamTool, ToolPath

RAPID_MM_MIN = 6000.0   # used for the time estimate only
_SAME_XY = 1e-6


@dataclass
class GcodeInfo:
    path: str
    lines: int
    cut_mm: float      # length of all G1 moves (cutting and plunging)
    minutes: float     # rough time estimate
    z_min: float       # deepest Z in the file


def _f(v: float) -> str:
    """3 decimals, trailing zeros stripped, never '-0'."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "", "-") else s


def _comment(text: str) -> str:
    """NC comments end at ')', so brackets inside the text are swapped."""
    return str(text).replace("(", "[").replace(")", "]").replace("\n", " ").replace("\r", " ")


def write_ncstudio(path: str, paths: list[ToolPath], tool: CamTool, *, title: str,
                   sheet_w: float, sheet_h: float, safe_z: float) -> GcodeInfo:
    """Write the paths that belong to `tool` (tool_id == tool.id) in the given
    order. Between consecutive paths that start where the last one ended (the
    next depth level of the same ring) the bit goes straight down without
    lifting; everything else lifts to safe_z, travels, and plunges."""
    if safe_z <= 0:
        raise ValueError("safe_z must be above the material top (Z0)")
    kind = {"vbit": "VBIT", "ball": "BALL"}.get(tool.kind, "FLAT")
    angle = f" {_f(tool.angle_deg)}deg" if tool.kind == "vbit" and tool.angle_deg else ""
    out = [
        f"(TITLE {_comment(title)})",
        f"(TOOL {_comment(tool.name)} D{_f(tool.diameter_mm)} {kind}{angle}"
        f" - load this bit, then set Z0 on top of material)",
        f"(SHEET {_f(sheet_w)} x {_f(sheet_h)} mm, origin bottom-left, Z0 = material top)",
        "G21 G90 G17",
        f"G0 Z{_f(safe_z)}",
        f"M03 S{int(tool.rpm)}",
        "G4 P3",                       # let the spindle reach speed
    ]
    feed, plunge = float(tool.feed_mm_min), float(tool.plunge_mm_min)
    cut = plunge_len = rapid = 0.0
    x, y, z = 0.0, 0.0, safe_z        # current position
    z_min = 0.0
    feed_now: float | None = None     # the F value the controller holds now

    for tp in paths:
        if tp.tool_id != tool.id or not tp.points:
            continue
        x0, y0, z0 = (float(v) for v in tp.points[0])
        if z < safe_z and math.hypot(x0 - x, y0 - y) <= _SAME_XY:
            # next level of the same ring: straight down (or up) without lifting
            if abs(z0 - z) > 1e-9:
                out.append(f"G1 Z{_f(z0)} F{_f(plunge)}")
                plunge_len += abs(z - z0)
                feed_now = plunge
        else:
            if z < safe_z:
                out.append(f"G0 Z{_f(safe_z)}")
                rapid += safe_z - z
            out.append(f"G0 X{_f(x0)} Y{_f(y0)}")
            rapid += math.hypot(x0 - x, y0 - y)
            out.append(f"G1 Z{_f(z0)} F{_f(plunge)}")
            plunge_len += safe_z - z0
            feed_now = plunge
        x, y, z = x0, y0, z0
        z_min = min(z_min, z0)
        for p in tp.points[1:]:
            nx, ny, nz = (float(v) for v in p)
            d = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2)
            if d < 1e-9:
                continue
            if nz < z and math.hypot(nx - x, ny - y) <= _SAME_XY:
                # straight down (end of a tab): plunge feed, not cutting feed
                out.append(f"G1 Z{_f(nz)} F{_f(plunge)}")
                feed_now = plunge
                plunge_len += z - nz
                z = nz
                z_min = min(z_min, nz)
                continue
            words = f"X{_f(nx)} Y{_f(ny)} Z{_f(nz)}"
            if feed_now != feed:
                out.append(f"G1 {words} F{_f(feed)}")
                feed_now = feed
            else:
                out.append(words)
            cut += d
            x, y, z = nx, ny, nz
            z_min = min(z_min, nz)

    if z < safe_z:
        rapid += safe_z - z
    rapid += math.hypot(x, y)
    out += [f"G0 Z{_f(safe_z)}", "M05", "G0 X0 Y0", "M30"]
    with open(path, "w", newline="\r\n") as fh:
        fh.write("\n".join(out) + "\n")
    minutes = cut / feed + plunge_len / plunge + rapid / RAPID_MM_MIN
    return GcodeInfo(path, len(out), cut + plunge_len, minutes, z_min)


__all__ = ["GcodeInfo", "write_ncstudio"]
