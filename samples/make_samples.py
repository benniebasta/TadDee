"""Write the sample cut files used by tests and demos.

Run:  python samples/make_samples.py
Writes samples/parts.svg, samples/letters.svg and samples/parts.dxf.
All geometry is our own, drawn here from simple numbers.
"""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf

HERE = Path(__file__).resolve().parent


def _circle_d(cx: float, cy: float, r: float) -> str:
    """A circle as two SVG arc commands (so it is a path subpath, not a <circle>)."""
    return (f"M{cx - r} {cy} A{r} {r} 0 1 0 {cx + r} {cy} "
            f"A{r} {r} 0 1 0 {cx - r} {cy} Z")


def _star_points(cx: float, cy: float, r_out: float, r_in: float, n: int = 5) -> str:
    """Points of an n-pointed star, first point straight up."""
    pts = []
    for k in range(2 * n):
        r = r_out if k % 2 == 0 else r_in
        a = -math.pi / 2 + k * math.pi / n
        pts.append(f"{cx + r * math.cos(a):.3f},{cy + r * math.sin(a):.3f}")
    return " ".join(pts)


def parts_svg() -> str:
    """Mixed test sheet in mm: plate with hole, ring with island, star, rounded rect, blob, open line."""
    plate = f"M20 20 H220 V140 H20 Z {_circle_d(170, 80, 25)}"
    ring = f"{_circle_d(330, 90, 60)} {_circle_d(330, 90, 40)}"
    blob = ("M60 230 C 90 180, 170 190, 180 240 C 190 290, 150 330, 110 320 "
            "C 60 310, 30 280, 60 230 Z")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="600mm" height="400mm" viewBox="0 0 600 400">
  <!-- plate 200 x 120 with a round hole -->
  <path d="{plate}" fill="#000" fill-rule="evenodd"/>
  <!-- blue line inside the plate: a score mark -->
  <line x1="40" y1="60" x2="120" y2="60" stroke="#0000ff" stroke-width="0.5"/>
  <!-- ring "O" and an island disc in its hole -->
  <path d="{ring}" fill="#000" fill-rule="evenodd"/>
  <circle cx="330" cy="90" r="20" fill="#000"/>
  <!-- star, placed with a transform -->
  <g transform="translate(500 90) rotate(15)">
    <polygon points="{_star_points(0, 0, 60, 25)}" fill="#000"/>
  </g>
  <!-- rounded rectangle -->
  <rect x="250" y="200" width="160" height="90" rx="15" ry="15" fill="#000"/>
  <!-- bezier blob -->
  <path d="{blob}" fill="#000"/>
</svg>
"""


# Block letters about 150 mm tall, drawn on a 0..150 grid, Y down (SVG).
LETTER_A = ("M0 150 L45 0 L85 0 L130 150 L95 150 L86 118 L44 118 L35 150 Z "
            "M52 90 L78 90 L65 42 Z")
LETTER_D = ("M0 0 L55 0 C 115 0, 130 40, 130 75 C 130 110, 115 150, 55 150 L0 150 Z "
            "M32 30 L52 30 C 85 30, 97 50, 97 75 C 97 100, 85 120, 52 120 L32 120 Z")
LETTER_O = ("M65 0 A65 75 0 1 0 65 150 A65 75 0 1 0 65 0 Z "
            "M65 32 A33 43 0 1 1 65 118 A33 43 0 1 1 65 32 Z")
LETTER_N = "M0 150 L0 0 L32 0 L98 98 L98 0 L130 0 L130 150 L98 150 L32 52 L32 150 Z"


def letters_svg() -> str:
    """The word ADON as four filled letters with counters (holes)."""
    paths = []
    for i, d in enumerate((LETTER_A, LETTER_D, LETTER_O, LETTER_N)):
        paths.append(f'  <path transform="translate({20 + i * 155} 20)" d="{d}" '
                     f'fill="#000" fill-rule="evenodd"/>')
    body = "\n".join(paths)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="660mm" height="190mm" viewBox="0 0 660 190">
{body}
</svg>
"""


def parts_dxf(path: Path) -> None:
    """DXF in mm: bulged LWPOLYLINE, CIRCLE, 4-LINE rectangle with a hole, ARC+LINE slot."""
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    # Obround 160 x 60: two straight sides, two half-circle ends (bulge 1 = 180 degrees)
    msp.add_lwpolyline([(30, 0, 0), (130, 0, 1), (130, 60, 0), (30, 60, 1)],
                       format="xyb", close=True)
    # Free disc
    msp.add_circle((250, 30), 30)
    # Rectangle 150 x 100 drawn as 4 separate LINEs, with a round hole in it
    corners = [(0, 120), (150, 120), (150, 220), (0, 220)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        msp.add_line(a, b)
    msp.add_circle((75, 170), 20)
    # Slot 120 x 40: two ARCs (r = 20) joined by two LINEs
    msp.add_line((220, 120), (300, 120))
    msp.add_arc((300, 140), 20, -90, 90)
    msp.add_line((300, 160), (220, 160))
    msp.add_arc((220, 140), 20, 90, 270)
    doc.saveas(path)


def main() -> None:
    (HERE / "parts.svg").write_text(parts_svg(), encoding="utf-8")
    (HERE / "letters.svg").write_text(letters_svg(), encoding="utf-8")
    parts_dxf(HERE / "parts.dxf")
    print("wrote", ", ".join(str(HERE / n) for n in ("parts.svg", "letters.svg", "parts.dxf")))


if __name__ == "__main__":
    main()
