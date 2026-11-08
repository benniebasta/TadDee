"""Command line: 2D picture in, relief G-code out.

  python -m relief.cli logo.png --width 600 --material 18 --out job/

First run writes job/styles.json (rule-based guesses). Edit the heights or
profiles in it and run again with --styles job/styles.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .api import ReliefError, ReliefParams, run_relief


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", help="PNG or JPG artwork, few flat colours work best")
    p.add_argument("--width", type=float, required=True, help="finished width in mm")
    p.add_argument("--material", type=float, required=True, help="material thickness in mm")
    p.add_argument("--max-height", type=float, default=6.0, help="tallest relief for auto styles, mm")
    p.add_argument("--styles", help="styles.json from a previous run (edited)")
    p.add_argument("--colors", type=int, default=6, help="max colour regions")
    p.add_argument("--resolution", type=float, default=0.2, help="mm per pixel")
    p.add_argument("--rough", default="6flat", help="roughing bit, e.g. 6flat; 'none' to skip")
    p.add_argument("--finish", default="3ball", help="finishing bit, e.g. 3ball")
    p.add_argument("--rough-feed", type=float, default=3000)
    p.add_argument("--finish-feed", type=float, default=3500)
    p.add_argument("--rpm", type=int, default=18000)
    p.add_argument("--stepdown", type=float, default=2.0, help="roughing depth per layer, mm")
    p.add_argument("--allowance", type=float, default=0.5, help="left by roughing for finishing, mm")
    p.add_argument("--safe-z", type=float, default=5.0)
    p.add_argument("--out", default="job")
    a = p.parse_args(argv)

    styles = json.load(open(a.styles, encoding="utf-8"))["regions"] if a.styles else None
    params = ReliefParams(width_mm=a.width, material_mm=a.material, max_height_mm=a.max_height,
                          max_colors=a.colors, resolution_mm=a.resolution, rough=a.rough, finish=a.finish,
                          rough_feed=a.rough_feed, finish_feed=a.finish_feed, rpm=a.rpm,
                          stepdown_mm=a.stepdown, allowance_mm=a.allowance, safe_z=a.safe_z, styles=styles)
    try:
        r = run_relief(a.image, a.out, params)
    except ReliefError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"size {r['width_mm']:.0f} x {r['height_mm']:.0f} mm, relief depth {r['depth_mm']:.2f} mm, "
          f"styles in {os.path.join(a.out, 'styles.json')}")
    for f in r["files"]:
        print(f"{f['name']}: {f['tool']}, deepest Z{f['deepest_z']:.2f}, {f['lines']} lines, about {f['minutes']:.0f} min")
    print(f"detail the finishing bit cannot reach: up to {r['lost_detail_mm']:.2f} mm (see preview.png)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
