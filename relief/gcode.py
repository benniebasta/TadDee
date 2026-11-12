"""Write toolpaths as plain G-code for NC Studio (no tool changer).

One file per tool. The operator loads the bit, sets Z0 on top of the
material, and runs the file. Units mm, absolute coordinates, XY origin at
the bottom-left corner of the artwork.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .toolpath import Tool

RAPID_MM_MIN = 6000.0   # used for the time estimate only


@dataclass
class JobInfo:
    path: str
    tool: Tool
    lines: int
    cut_mm: float
    rapid_mm: float
    minutes: float
    z_min: float


def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def write_gcode(path: str, polylines: list[np.ndarray], tool: Tool, *, title: str,
                safe_z: float = 5.0, width_mm: float, height_mm: float,
                link_mm: float = 0.0) -> JobInfo:
    """polylines: list of (n,3) arrays in machining order.

    link_mm: when the next line starts within this XY distance, stay down:
    rise to the higher of the two Z values, step across, then go down. Used
    for zig-zag rows a stepover apart; anything further lifts to safe_z.
    """
    if safe_z <= 0:
        raise ValueError("safe_z must be above the material top (Z0)")
    out = [
        f"(TITLE {title})",
        f"(TOOL {tool.name} D{_f(tool.diameter_mm)} {'BALL' if tool.ball else 'FLAT'} - load this bit, then set Z0 on top of material)",
        f"(SIZE X0..{_f(width_mm)} Y0..{_f(height_mm)} mm, origin bottom-left)",
        "G21 G90 G17",
        f"G0 Z{_f(safe_z)}",
        f"M03 S{tool.rpm}",
        "G4 P3",                       # let the spindle reach speed
    ]
    cut = rapid = plunge = 0.0   # plunge = length moved at the plunge feed (time estimate)
    pos = np.array([0.0, 0.0, safe_z])
    z_min = 0.0
    feed_now = None
    for ln in polylines:
        if len(ln) == 0:
            continue
        x0, y0, z0 = ln[0]
        hop = float(np.hypot(x0 - pos[0], y0 - pos[1]))
        if pos[2] < safe_z and hop <= link_mm:
            # short link between neighbouring rows: up, across, down
            zl = max(pos[2], z0)
            if zl > pos[2]:
                out.append(f"G1 Z{_f(zl)} F{_f(tool.plunge_mm_min)}")
            out.append(f"G1 X{_f(x0)} Y{_f(y0)} F{_f(tool.feed_mm_min)}")
            if z0 < zl:
                out.append(f"G1 Z{_f(z0)} F{_f(tool.plunge_mm_min)}")
            cut += hop + abs(zl - pos[2]) + abs(zl - z0)
            plunge += abs(zl - pos[2]) + abs(zl - z0)
            feed_now = tool.plunge_mm_min if z0 < zl else tool.feed_mm_min
        else:
            # lift, travel, plunge
            if pos[2] < safe_z:
                out.append(f"G0 Z{_f(safe_z)}")
                rapid += safe_z - pos[2]
            out.append(f"G0 X{_f(x0)} Y{_f(y0)}")
            rapid += hop
            out.append(f"G1 Z{_f(z0)} F{_f(tool.plunge_mm_min)}")
            cut += safe_z - z0
            plunge += safe_z - z0
            feed_now = tool.plunge_mm_min
        prev = np.array([x0, y0, z0])
        for x, y, z in ln[1:]:
            if feed_now != tool.feed_mm_min:
                out.append(f"G1 X{_f(x)} Y{_f(y)} Z{_f(z)} F{_f(tool.feed_mm_min)}")
                feed_now = tool.feed_mm_min
            else:
                out.append(f"X{_f(x)} Y{_f(y)} Z{_f(z)}")
            cut += float(np.linalg.norm(np.array([x, y, z]) - prev))
            prev = np.array([x, y, z])
        z_min = min(z_min, float(ln[:, 2].min()))
        pos = np.array([float(v) for v in prev])
    out += [f"G0 Z{_f(safe_z)}", "M05", "G0 X0 Y0", "M30", ""]
    with open(path, "w", newline="\r\n") as fh:   # NC Studio runs on Windows
        fh.write("\n".join(out))
    minutes = ((cut - plunge) / tool.feed_mm_min + plunge / tool.plunge_mm_min
               + rapid / RAPID_MM_MIN + 3 / 60)   # + G4 P3 spindle wait
    return JobInfo(path, tool, len(out), cut, rapid, minutes, z_min)
