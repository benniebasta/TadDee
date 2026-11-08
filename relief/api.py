"""One call that runs the whole relief job. Used by the CLI and the web app."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .gcode import write_gcode
from .heightmap import RegionStyle, auto_styles, build_heightmap, load_artwork
from .preview import save_greyscale, save_preview, save_toolpath_plot
from .toolpath import Tool, finishing, roughing


class ReliefError(ValueError):
    """Bad input the user can fix (shown as a message, not a crash)."""


@dataclass
class ReliefParams:
    width_mm: float
    material_mm: float
    max_height_mm: float = 6.0
    max_colors: int = 6
    resolution_mm: float = 0.25
    rough: str = "6flat"          # "<dia>flat" | "<dia>ball" | "none"
    finish: str = "3ball"
    rough_feed: float = 3000.0
    finish_feed: float = 3500.0
    plunge_feed: float = 800.0
    rpm: int = 18000
    stepdown_mm: float = 2.0
    allowance_mm: float = 0.5
    safe_z: float = 5.0
    styles: list[dict] | None = None   # one dict per colour region; None = auto rules


def parse_tool(spec: str, role: str, feed: float, rpm: int, plunge: float = 800.0) -> Tool:
    """'6flat' or '3ball' -> Tool."""
    s = spec.lower().strip()
    if not (s.endswith("ball") or s.endswith("flat")):
        raise ReliefError(f"bit {spec!r}: write it like 6flat or 3ball")
    ball = s.endswith("ball")
    try:
        dia = float(s[:-4])
    except ValueError as e:
        raise ReliefError(f"bit {spec!r}: diameter is not a number") from e
    return Tool(f"{role} {dia:g}mm {'ball' if ball else 'flat'}", dia, ball=ball,
                feed_mm_min=feed, plunge_mm_min=plunge, rpm=rpm)


def run_relief(image_path: str, out_dir: str, p: ReliefParams, base: str | None = None) -> dict:
    """Make styles.json, preview.png, finish_path.png, <base>_relief16.png and
    one .nc per bit in out_dir. Returns a summary dict (JSON-safe)."""
    os.makedirs(out_dir, exist_ok=True)
    base = base or os.path.splitext(os.path.basename(image_path))[0]
    try:
        art = load_artwork(image_path, p.width_mm, p.resolution_mm, p.max_colors)
    except ValueError as e:
        raise ReliefError(str(e)) from e

    if p.styles:
        if len(p.styles) != len(art.colors):
            raise ReliefError(f"styles have {len(p.styles)} regions, artwork has {len(art.colors)}")
        try:
            styles = [RegionStyle(**{k: r[k] for k in ("profile", "height_mm", "round_mm", "base_mm") if k in r})
                      for r in p.styles]
        except (TypeError, ValueError) as e:
            raise ReliefError(str(e)) from e
    else:
        styles = auto_styles(art, p.max_height_mm)
    regions = [{"index": i, "color": "#%02x%02x%02x" % c, "area_mm2": round(n * art.px_mm ** 2), **asdict(s)}
               for i, (c, n, s) in enumerate(zip(art.colors, art.area_px, styles))]
    with open(os.path.join(out_dir, "styles.json"), "w", encoding="utf-8") as fh:
        json.dump({"regions": regions}, fh, indent=2)

    H = build_heightmap(art, styles)
    depth = float(H.max() - H.min())
    if depth >= p.material_mm - 1.0:
        raise ReliefError(f"relief is {depth:.1f} mm deep but material is {p.material_mm:g} mm: lower the heights")

    fin = parse_tool(p.finish, "finish", p.finish_feed, p.rpm, p.plunge_feed)
    jobs, floor = [], None
    if p.rough != "none":
        rt = parse_tool(p.rough, "rough", p.rough_feed, p.rpm, p.plunge_feed)
        rt.stepdown_mm = p.stepdown_mm
        levels, floor = roughing(H, art.px_mm, rt, top_z=0.0, allowance_mm=p.allowance_mm)
        jobs.append(write_gcode(os.path.join(out_dir, f"{base}_1_rough_{p.rough}.nc"),
                                [ln for lv in levels for ln in lv], rt, title=f"{base} roughing",
                                safe_z=p.safe_z, width_mm=art.width_mm, height_mm=art.height_mm,
                                link_mm=1.5 * rt.stepover_mm))
    fin_lines = finishing(H, art.px_mm, fin, top_z=0.0, skip=floor)
    jobs.append(write_gcode(os.path.join(out_dir, f"{base}_2_finish_{p.finish}.nc"), fin_lines, fin,
                            title=f"{base} finishing", safe_z=p.safe_z, width_mm=art.width_mm,
                            height_mm=art.height_mm, link_mm=1.5 * fin.stepover_mm))
    lost = save_preview(os.path.join(out_dir, "preview.png"), art, H, fin,
                        title=f"{base}: {art.width_mm:g} x {art.height_mm:.0f} mm")
    save_toolpath_plot(os.path.join(out_dir, "finish_path.png"), fin_lines, art.width_mm, art.height_mm,
                       "Finishing path (top view)")
    save_greyscale(os.path.join(out_dir, f"{base}_relief16.png"), H)
    return {
        "width_mm": round(art.width_mm, 2), "height_mm": round(art.height_mm, 2),
        "depth_mm": round(depth, 2), "lost_detail_mm": round(lost, 2), "regions": regions,
        "files": [{"name": os.path.basename(j.path), "tool": j.tool.name, "minutes": round(j.minutes, 1),
                   "lines": j.lines, "deepest_z": round(j.z_min, 3)} for j in jobs],
        "images": ["preview.png", "finish_path.png", f"{base}_relief16.png"],
        "total_minutes": round(sum(j.minutes for j in jobs), 1),
    }
