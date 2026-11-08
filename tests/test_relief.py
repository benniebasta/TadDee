import re

import numpy as np
import pytest
from PIL import Image, ImageDraw

from relief.gcode import write_gcode
from relief.heightmap import RegionStyle, auto_styles, build_heightmap, load_artwork
from relief.toolpath import Tool, _footprint, finishing, roughing


@pytest.fixture
def art(tmp_path):
    im = Image.new("RGB", (400, 200), "white")
    d = ImageDraw.Draw(im)
    d.ellipse((40, 40, 160, 160), fill=(200, 30, 30))      # wide -> dome
    d.rectangle((220, 90, 380, 98), fill=(20, 20, 20))      # thin bar -> flat
    p = tmp_path / "a.png"
    im.save(p)
    return load_artwork(str(p), width_mm=100, resolution_mm=0.25)


def test_regions_ignore_antialias_fringe(art):
    assert len(art.colors) == 3


def test_auto_styles_rules(art):
    st = auto_styles(art, max_height_mm=6)
    kinds = sorted(s.profile for s in st)
    assert kinds == ["background", "dome", "flat"]


def test_dome_and_engrave_heights(art):
    st = auto_styles(art, 6)
    H = build_heightmap(art, st, smooth_mm=0)
    assert H.max() == pytest.approx(6, abs=0.05)
    assert H.min() == 0
    st = [RegionStyle("engrave", 2) if s.profile != "background" else s for s in st]
    assert build_heightmap(art, st, smooth_mm=0).min() == pytest.approx(-2)


def test_bad_style_rejected():
    with pytest.raises(ValueError):
        RegionStyle("wavy")


def _carve(shape, px_mm, polylines, tool):
    """Lowest point the tool reaches at every pixel, sampling the paths densely."""
    cut = np.full(shape, np.inf)
    r = tool.diameter_mm / 2
    rp = int(np.ceil(r / px_mm))
    xx, yy, inside = _footprint(rp)
    lift = r - np.sqrt(np.clip(r ** 2 - (np.hypot(xx, yy) * px_mm) ** 2, 0, None)) if tool.ball else 0 * xx
    lift = np.where(inside, lift, np.inf)
    for ln in polylines:
        for a, b in zip(ln[:-1], ln[1:]):
            n = max(2, int(np.ceil(np.linalg.norm(b[:2] - a[:2]) / px_mm)) + 1)
            for t in np.linspace(0, 1, n):
                x, y, z = a + (b - a) * t
                c, rr = int(round(x / px_mm)), shape[0] - 1 - int(round(y / px_mm))
                r0, r1 = max(0, rr - rp), min(shape[0], rr + rp + 1)
                c0, c1 = max(0, c - rp), min(shape[1], c + rp + 1)
                win = lift[r0 - rr + rp:r1 - rr + rp, c0 - c + rp:c1 - c + rp] + z
                np.minimum(cut[r0:r1, c0:c1], win, out=cut[r0:r1, c0:c1])
    return cut


def test_no_gouge_finish_and_rough(tmp_path):
    im = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(im).ellipse((20, 15, 100, 65), fill=(0, 0, 0))
    p = tmp_path / "s.png"
    im.save(p)
    a = load_artwork(str(p), width_mm=30, resolution_mm=0.25)
    H = build_heightmap(a, auto_styles(a, 4))
    design = H - H.max()          # Z of the finished surface, top = 0
    ball = Tool("b", 3, ball=True)
    cut = _carve(H.shape, a.px_mm, finishing(H, a.px_mm, ball, 0.0), ball)
    assert (cut - design).min() > -0.02, "finishing cuts into the design"
    flat = Tool("f", 6, ball=False, stepdown_mm=1.5)
    levels, floor = roughing(H, a.px_mm, flat, 0.0, allowance_mm=0.5)
    cut = _carve(H.shape, a.px_mm, [ln for lv in levels[:-1] for ln in lv], flat)
    assert (cut - design).min() > 0.5 - 0.02, "roughing eats the allowance"
    assert floor is not None and floor.any()


def test_gcode_for_nc_studio(tmp_path, art):
    H = build_heightmap(art, auto_styles(art, 5))
    ball = Tool("b", 3, ball=True)
    out = tmp_path / "f.nc"
    info = write_gcode(str(out), finishing(H, art.px_mm, ball, 0.0), ball, title="t",
                       width_mm=art.width_mm, height_mm=art.height_mm, link_mm=0.5)
    text = out.read_text()
    assert "G21 G90 G17" in text and text.rstrip().endswith("M30")
    assert "M06" not in text and "M6 " not in text and " T1" not in text
    zs = [float(z) for z in re.findall(r"Z(-?[\d.]+)", text)]
    assert max(zs) <= 5 and min(zs) == pytest.approx(-5, abs=0.05)
    assert info.minutes > 0
