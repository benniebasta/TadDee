"""Turn 2D artwork into a height map (mm), one profile per colour region."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from scipy import ndimage

PROFILES = ("flat", "dome", "bevel", "engrave", "background")


@dataclass
class RegionStyle:
    profile: str = "dome"     # flat | dome | bevel | engrave | background
    height_mm: float = 5.0    # top of the region above the background (engrave: depth below)
    round_mm: float = 8.0     # dome/bevel: how far in from the edge the shape keeps rising
    base_mm: float = 0.0      # dome/bevel: height at the very edge (a small wall)

    def __post_init__(self):
        if self.profile not in PROFILES:
            raise ValueError(f"unknown profile {self.profile!r}, use one of {PROFILES}")
        if self.height_mm < 0 or self.round_mm <= 0 or self.base_mm < 0:
            raise ValueError("height_mm and base_mm must be >= 0, round_mm > 0")


@dataclass
class Artwork:
    labels: np.ndarray            # int region id per pixel, 0..n-1
    colors: list[tuple[int, int, int]]
    px_mm: float                  # size of one pixel in mm
    width_mm: float
    height_mm: float
    area_px: list[int] = field(default_factory=list)


def load_artwork(path: str, width_mm: float, resolution_mm: float = 0.2,
                 max_colors: int = 6) -> Artwork:
    """Read PNG/JPG, scale so one pixel = resolution_mm, split into colour regions."""
    if width_mm <= 0 or resolution_mm <= 0:
        raise ValueError("width_mm and resolution_mm must be > 0")
    img = Image.open(path).convert("RGBA")
    # transparent pixels count as white background
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    w_px = max(2, round(width_mm / resolution_mm))
    h_px = max(2, round(img.height * w_px / img.width))
    if w_px * h_px > 40_000_000:
        raise ValueError("artwork too large at this resolution; use a coarser resolution_mm")
    img = img.resize((w_px, h_px), Image.LANCZOS)
    labels, colors = _split_colours(np.asarray(img, dtype=np.float32), max_colors)
    px_mm = width_mm / w_px
    area = [int((labels == i).sum()) for i in range(len(colors))]
    return Artwork(labels, colors, px_mm, width_mm, h_px * px_mm, area)


def _split_colours(rgb: np.ndarray, max_colors: int, min_share: float = 0.005,
                   merge_dist: float = 40.0):
    """Main colours of the artwork, with anti-aliased edge pixels and near
    duplicates folded into the closest main colour."""
    img = Image.fromarray(rgb.astype(np.uint8))
    q = img.quantize(colors=max_colors * 3, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    lab = np.asarray(q)
    pal = np.array(q.getpalette()[: 3 * max_colors * 3], dtype=np.float32).reshape(-1, 3)
    # rank colours by their solid core (2 px in from every edge): anti-aliased
    # fringes are thin, so they have almost no core and drop out
    core = np.array([ndimage.binary_erosion(lab == k, iterations=2).sum() for k in range(len(pal))])
    order = np.argsort(-core)
    main: list[np.ndarray] = []
    for i in order:
        if core[i] < min_share * lab.size or len(main) >= max_colors:
            break
        if all(np.linalg.norm(pal[i] - m) > merge_dist for m in main):
            main.append(pal[i])
    centres = np.array(main)
    # every pixel goes to its nearest main colour
    d = np.stack([((rgb - c) ** 2).sum(-1) for c in centres])
    labels = d.argmin(0).astype(np.int32)
    # drop specks smaller than ~3x3 px by a majority (mode) filter
    votes = np.stack([ndimage.uniform_filter((labels == k).astype(np.float32), size=3)
                      for k in range(len(centres))])
    labels = votes.argmax(0).astype(np.int32)
    colors = [tuple(int(v) for v in c) for c in centres]
    return labels, colors


def auto_styles(art: Artwork, max_height_mm: float = 6.0) -> list[RegionStyle]:
    """Rule-based starting point (no AI): lightest big region is background,
    thin regions are flat raised, wide regions are domed. User edits after."""
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in art.colors]
    bg = max(range(len(art.colors)), key=lambda i: (lum[i] > 200, art.area_px[i]))
    styles = []
    for i in range(len(art.colors)):
        if i == bg:
            styles.append(RegionStyle("background", 0.0))
            continue
        mask = art.labels == i
        # typical half-width of the strokes in mm
        dist = ndimage.distance_transform_edt(mask) * art.px_mm
        half_w = float(np.percentile(dist[mask], 95)) if mask.any() else 0.0
        if half_w < 2.0:
            styles.append(RegionStyle("flat", round(max_height_mm * 0.5, 2)))
        else:
            styles.append(RegionStyle("dome", max_height_mm, round_mm=round(min(half_w, 25.0), 2)))
    return styles


def build_heightmap(art: Artwork, styles: list[RegionStyle], smooth_mm: float = 0.4) -> np.ndarray:
    """Height in mm above the background for every pixel (engrave goes negative)."""
    if len(styles) != len(art.colors):
        raise ValueError(f"need {len(art.colors)} styles, got {len(styles)}")
    H = np.zeros(art.labels.shape, dtype=np.float64)
    for i, st in enumerate(styles):
        mask = art.labels == i
        if not mask.any() or st.profile == "background":
            continue
        if st.profile == "flat":
            H[mask] = st.height_mm
            continue
        if st.profile == "engrave":
            H[mask] = -st.height_mm
            continue
        d = ndimage.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1] * art.px_mm
        t = np.clip(d / st.round_mm, 0.0, 1.0)
        if st.profile == "dome":
            shape = np.sqrt(1.0 - (1.0 - t) ** 2)          # quarter circle, round shoulder
        else:  # bevel
            shape = t                                      # straight chamfer
        H[mask] = (st.base_mm + (st.height_mm - st.base_mm) * shape)[mask]
    if smooth_mm > 0:
        H = ndimage.gaussian_filter(H, sigma=smooth_mm / art.px_mm)
    return H
