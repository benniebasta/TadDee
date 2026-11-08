# Nest app

Our own nesting app for the factory: SVG/DXF parts in, packed sheets out,
NC Studio G-code (router, no tool changer) or Ruida DXF (laser), offcut
library, cost estimate, and 3D relief from a picture. Full spec: PLAN.md.

## Start
    pip install -r requirements.txt
    python -m app            # then open http://localhost:8040  (Windows: run.bat)
Tests: `python -m pytest -q` (108 unit/API tests). Browser test: start the
app on an empty data folder, then `node e2e/e2e.mjs`.

# 3D relief module

Turns a flat 2D picture (logo, sign artwork) into a carved 3D relief and
writes G-code for an NC Studio router with no tool changer. Plain program
code: no AI, same input always gives the same output.

## Run
    pip install -r requirements.txt
    python -m relief.cli samples/sign.png --width 600 --material 18 --out job

Output in `job/`:
- `<name>_1_rough_6flat.nc`: roughing with a 6 mm flat bit, layers of 2 mm,
  leaves 0.5 mm, then cleans the open background to exact depth.
- `<name>_2_finish_3ball.nc`: finishing with a 3 mm ball-nose bit, raised
  shapes only.
- `styles.json`: one entry per colour. Change `profile` (dome, flat, bevel,
  engrave, background), `height_mm`, `round_mm`, then run again with
  `--styles job/styles.json`.
- `preview.png`: artwork, designed relief, and what the finishing bit can
  really cut. `finish_path.png`: where the ball bit goes.
- `<name>_relief16.png`: 16-bit grey height map (white = highest), which
  ArtCAM and other CAM can import as a relief.

## At the machine
Each file is one bit. Load the bit named in the file's first lines, set Z0
on the top of the material and XY0 at the bottom-left corner of the
artwork, run the rough file, change the bit, set Z0 again, run the finish.

## How it works
1. Colours: the picture is split into its main colours; anti-aliased edges
   fold into the nearest main colour.
2. Shape per colour: dome (rounded), flat (raised plateau), bevel
   (chamfer), engrave (cut in), background. Auto rules: lightest big area is
   background, strokes under 4 mm wide are flat, wider areas are domes.
3. Toolpaths: drop-cutter over the height map, so the bit never cuts below
   the designed surface (checked by `tests/test_relief.py`).
4. G-code: G21 G90 G17, M03 S, G0/G1 only, no M06, Windows line endings.

Next: vector input (SVG/DXF), raster angle choice, nesting module, Ruida.
