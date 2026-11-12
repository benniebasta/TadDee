# Nest app

Our own nesting app for the factory: SVG/DXF parts in, packed sheets out,
NC Studio G-code (router, no tool changer) or Ruida DXF (laser), offcut
library, cost estimate, and 3D relief from a picture. Full spec: PLAN.md.

## Start
    pip install -r requirements.txt
    python -m app            # then open http://localhost:8040  (Windows: run.bat)
Tests: `python -m pytest -q` (127 unit/API tests). Browser test: start the
app on an empty data folder, then `node e2e/e2e.mjs`.

## Running it in the shop
- **Data folder** (`NEST_DATA_DIR`, default `./data`): `nest.db`, `jobs/<id>/`
  (exported files), `relief/<id>/`, `logs/nest.log`.
- **Background work:** nesting and relief run in a worker inside the app,
  one at a time per kind. The page shows progress and Cancel and picks the
  task up again after a reload or from another device. API: add `?async=1`
  to `POST /api/jobs/{id}/nest`, `POST /api/relief`, `POST /api/relief/{id}`;
  then `GET /api/tasks/{task_id}`, `POST /api/tasks/{task_id}/cancel`.
- **Database upgrades** run by themselves at start (Alembic). How to add a
  migration: `app/migrations/README.md`.
- **Logs:** `data/logs/nest.log`, 5 files of 5 MB. Every line of a request
  carries its request id; the same id is in the `X-Request-ID` header and in
  error replies, so an error on screen can be found in the log.
- **Health:** `GET /api/health` (database, free disk, version, uptime, last
  backup, tasks waiting); also on the Settings page.
- **Clean-up:** at start and every 24 h, exported files older than 90 days
  are deleted (never for jobs marked as cut), old relief runs too; jobs in
  the bin are deleted for good after 30 days. Offcuts are never touched.
- **Jobs:** Delete moves a job to the Bin (Jobs page, "Bin") for 30 days,
  with Restore. Duplicate copies parts and nest settings, not results.
  "Mark as cut" locks the job (no part, nest or export changes) until
  "Unlock", which also takes its saved offcuts back out of the library.

## Backup
    python -m app.backup                 # Windows: backup.bat
Makes `nest-YYYYMMDD-HHMMSS-mmm.zip` (a consistent copy of the database plus
all job and relief files) in `NEST_BACKUP_DIR` (default `D:\Site backups\nest`
on Windows, `./backups` elsewhere) and keeps the newest 14. Settings has a
"Back up now" button and shows the last backup time.

Every night at 23:30 (run once in a Command Prompt as the user that runs Nest,
with the real install folder):

    schtasks /Create /F /SC DAILY /ST 23:30 /TN "Nest backup" /TR "\"C:\nest\backup.bat\""

Restore (only into an empty or new folder, then start Nest on it):

    python -m app.backup --restore "D:\Site backups\nest\nest-20261112-233000-000.zip" --to C:\nest\data-restored
    set NEST_DATA_DIR=C:\nest\data-restored
    run.bat

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
