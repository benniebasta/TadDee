"""Pictures to check a job before it goes to the machine."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LightSource
from scipy import ndimage

from .heightmap import Artwork
from .toolpath import Tool, _footprint, tool_tip_surface


def machined_surface(H: np.ndarray, px_mm: float, tool: Tool) -> np.ndarray:
    """What the bit can actually leave behind (no scallops): fine detail
    narrower than the bit is lost. Same units and datum as H."""
    tip = tool_tip_surface(H, px_mm, tool)
    r_mm = tool.diameter_mm / 2
    r_px = max(1, int(np.ceil(r_mm / px_mm)))
    xx, yy, inside = _footprint(r_px)
    if tool.ball:
        d = np.hypot(xx, yy) * px_mm
        lift = np.where(inside, r_mm - np.sqrt(np.clip(r_mm ** 2 - d ** 2, 0, None)), 1e9)
    else:
        lift = np.where(inside, 0.0, 1e9)
    # lowest point of any tool position that covers this pixel
    return -ndimage.grey_dilation(-tip, structure=-lift, mode="nearest")


def _shade(Z, px_mm):
    """Wood-coloured hill shade, light from the top left."""
    ls = LightSource(azdeg=315, altdeg=35)
    hs = ls.hillshade(Z, vert_exag=2.5, dx=px_mm, dy=px_mm)
    wood = np.array([0.86, 0.68, 0.46])
    return np.clip(hs[..., None] * wood * 1.25, 0, 1)


def save_preview(path: str, art: Artwork, H: np.ndarray, finish_tool: Tool, title: str = ""):
    S = machined_surface(H, art.px_mm, finish_tool)
    lost = np.abs(S - H)
    asp = art.height_mm / art.width_mm
    fig, ax = plt.subplots(1, 3, figsize=(16, 16 / 3 * asp + 1.2))
    ext = [0, art.width_mm, 0, art.height_mm]
    pal = np.array(art.colors, dtype=float) / 255
    ax[0].imshow(pal[art.labels], extent=ext)
    ax[0].set_title("Artwork regions")
    ax[1].imshow(_shade(H, art.px_mm), extent=ext)
    ax[1].set_title(f"Designed relief (depth {H.max() - H.min():.1f} mm)")
    ax[2].imshow(_shade(S, art.px_mm), extent=ext)
    ax[2].set_title(f"After {finish_tool.name} (lost detail up to {lost.max():.2f} mm)")
    for a in ax:
        a.set_xlabel("mm")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return float(lost.max())


def save_toolpath_plot(path: str, polylines, width_mm, height_mm, title: str):
    fig, ax = plt.subplots(figsize=(8, 8 * height_mm / max(width_mm, 1)))
    for ln in polylines:
        ax.plot(ln[:, 0], ln[:, 1], lw=0.3, color="#b5651d")
    ax.set_xlim(0, width_mm); ax.set_ylim(0, height_mm); ax.set_aspect("equal")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def save_greyscale(path: str, H: np.ndarray):
    """16-bit grey PNG (white = highest). ArtCAM and most CAM can import it as a relief."""
    from PIL import Image
    span = max(H.max() - H.min(), 1e-9)
    g = ((H - H.min()) / span * 65535).astype(np.uint16)
    Image.fromarray(g).save(path)
