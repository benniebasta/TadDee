// Tests for web/sim_parse.js (G-code simulator parser). Run: node tests_js/sim_parse.test.mjs
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseGcode, createParser, segmentAt, RAPID_MM_MIN } from '../web/sim_parse.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log(`PASS ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}\n  ${e.stack || e}`); }
}
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg || ''} ${a} != ${b} (tol ${tol})`);
const codes = (r) => r.warnings.map((w) => w.code).sort();
const last = (r) => [r.px[r.n], r.py[r.n], r.pz[r.n]];

// ---- our own writer, read back: time estimate within 1 % of cam/ncstudio.py ----
function writeWithPython(out) {
  const py = `
import sys
sys.path.insert(0, ${JSON.stringify(root)})
from cam.ncstudio import write_ncstudio
from cam.toolpaths import CamTool, ToolPath
import math, json
tool = CamTool(id="t6", name="6 mm flat", diameter_mm=6, feed_mm_min=3000, plunge_mm_min=800, rpm=18000)
paths = []
for k, (cx, cy) in enumerate([(100, 100), (300, 120), (520, 300)]):
    ring = [(cx + 40 * math.cos(a / 64 * 2 * math.pi), cy + 40 * math.sin(a / 64 * 2 * math.pi)) for a in range(65)]
    for z in (-1.1, -2.2, -3.3):
        paths.append(ToolPath("t6", [(x, y, z) for x, y in ring], "outside", f"p{k}", 0))
info = write_ncstudio(${JSON.stringify(out)}, paths, tool, title="read back", sheet_w=1220, sheet_h=610, safe_z=5)
print(json.dumps({"minutes": info.minutes, "lines": info.lines, "cut": info.cut_mm, "z_min": info.z_min}))
`;
  for (const exe of ['python', 'python3']) {
    const p = spawnSync(exe, ['-c', py], { encoding: 'utf8' });
    if (p.status === 0) return JSON.parse(p.stdout.trim().split('\n').pop());
  }
  return null;
}

const tmp = path.join(os.tmpdir(), `nest-sim-${process.pid}.nc`);
const info = writeWithPython(tmp);
if (info) fs.copyFileSync(tmp, path.join(root, 'tests_js', 'tmp-sample.nc'));
else fs.writeFileSync(path.join(root, 'tests_js', 'tmp-sample.nc'), 'G21 G90\r\nM3\r\nG0 Z5\r\nG0 X10 Y10\r\nG1 Z-1 F800\r\nG1 X20 F3000\r\nG0 Z5\r\nM30\r\n');

test('straight moves, rapid vs feed length and time', () => {
  const r = parseGcode('G21 G90\nG0 Z5\nG0 X10 Y0\nG1 Z-1 F600\nG1 X40 F1200\nY40\nG0 Z5\nM30\n');
  near(r.stats.rapidMm, 10 + 6, 1e-9, 'rapid');
  near(r.stats.cutMm, 6 + 30 + 40, 1e-9, 'cut');
  near(r.stats.plungeMm, 6, 1e-9, 'plunge');
  near(r.stats.minutes, 16 / RAPID_MM_MIN + 6 / 600 + 70 / 1200, 1e-9, 'minutes');
  assert.equal(r.stats.lines, 8);
  near(r.stats.zMin, -1, 1e-6);
  assert.deepEqual(codes(r), ['spindle_off']);    // no M3 before cutting
});

test('G2 / G3 arcs with I J: quarter, half and full circles', () => {
  // quarter circle radius 10 clockwise from (0,10) to (10,0), centre (0,0)
  let r = parseGcode('M3\nG0 X0 Y10 Z0\nG2 X10 Y0 I0 J-10 F1000\nM30');
  near(r.stats.xyCutMm, Math.PI * 10 / 2, 1e-9, 'quarter cw');
  near(r.px[r.n], 10, 1e-4); near(r.py[r.n], 0, 1e-4);
  // the arc points stay on the circle and pass through (7.07, 7.07)
  for (let i = 1; i <= r.n; i++) near(Math.hypot(r.px[i], r.py[i]), 10, 1e-3, 'on circle');
  const mid = Math.floor(r.n / 2) + 1;
  assert.ok(r.px[mid] > 5 && r.py[mid] > 5, 'cw from 12 to 3 o\'clock goes through the upper right');
  // ccw quarter from (10,0) to (0,10): also upper right
  r = parseGcode('M3\nG0 X10 Y0 Z0\nG3 X0 Y10 I-10 J0 F1000\nM30');
  near(r.stats.xyCutMm, Math.PI * 5, 1e-9, 'quarter ccw');
  const m2 = Math.floor(r.n / 2) + 1;
  assert.ok(r.px[m2] > 5 && r.py[m2] > 5);
  // G2 from (10,0) to (0,10) clockwise is the long way round (3/4)
  r = parseGcode('M3\nG0 X10 Y0 Z0\nG2 X0 Y10 I-10 J0 F1000\nM30');
  near(r.stats.xyCutMm, Math.PI * 15, 1e-9, 'three quarters');
  // full circle: same start and end
  r = parseGcode('M3\nG0 X10 Y0 Z0\nG2 X10 Y0 I-10 J0 F500\nM30');
  near(r.stats.xyCutMm, 2 * Math.PI * 10, 1e-9, 'full circle');
  near(r.stats.minutes, (2 * Math.PI * 10) / 500 + 10 / RAPID_MM_MIN, 1e-9);
  assert.equal(r.stats.cutBounds.x0 < -9.99, true, 'arc bounds include the far side');
  assert.deepEqual(codes(r), []);
});

test('arcs with R (positive = short way, negative = long way) and helix', () => {
  let r = parseGcode('M3\nG0 X0 Y10 Z0\nG2 X10 Y0 R10 F1000\nM30');
  near(r.stats.xyCutMm, Math.PI * 5, 1e-6, 'R+ short');
  for (let i = 1; i <= r.n; i++) near(Math.hypot(r.px[i], r.py[i]), 10, 1e-3, 'centre at origin');
  r = parseGcode('M3\nG0 X0 Y10 Z0\nG2 X10 Y0 R-10 F1000\nM30');
  near(r.stats.xyCutMm, Math.PI * 15, 1e-6, 'R- long');
  r = parseGcode('M3\nG0 X0 Y10 Z0\nG3 X10 Y0 R10 F1000\nM30');
  near(r.stats.xyCutMm, Math.PI * 5, 1e-6, 'G3 R+');
  const mid = Math.floor(r.n / 2) + 1;
  assert.ok(r.px[mid] < 5 && r.py[mid] < 5, 'G3 from 12 to 3 o\'clock with R+ curves round the lower left');
  // helix: full turn while going down 2 mm
  r = parseGcode('M3\nG0 X10 Y0 Z0\nG2 X10 Y0 Z-2 I-10 J0 F1000\nM30');
  near(r.stats.xyCutMm, Math.hypot(2 * Math.PI * 10, 2), 1e-9, 'helix length');
  near(last(r)[2], -2, 1e-6);
  // bad radius: endpoint not on the circle
  r = parseGcode('M3\nG0 X10 Y0 Z0\nG2 X0 Y5 I-10 J0 F1000\nM30');
  assert.ok(codes(r).includes('bad_arc'));
});

test('relative mode G91 and absolute arc centres G90.1', () => {
  const r = parseGcode('M3\nG90 G0 X5 Y5 Z1\nG91\nG1 Z-2 F300\nX10\nY10\nX-10 Y-10\nG90\nG0 Z5\nM30');
  near(r.stats.zMin, -1, 1e-6);
  near(r.stats.cutMm, 2 + 10 + 10 + Math.hypot(10, 10), 1e-6);
  const [x, y, z] = last(r);
  near(x, 5, 1e-6); near(y, 5, 1e-6); near(z, 5, 1e-6);
  const a = parseGcode('M3\nG0 X10 Y0 Z0\nG90.1\nG3 X0 Y10 I0 J0 F1000\nM30');
  near(a.stats.xyCutMm, Math.PI * 5, 1e-9, 'absolute centre');
});

test('inch mode G20 converts coordinates and feed', () => {
  const r = parseGcode('G20 G90\nM3\nG0 X0 Y0 Z0.2\nG1 Z-0.1 F10\nG1 X1 F20\nM30');
  near(r.stats.cutMm, 0.3 * 25.4 + 25.4, 1e-6);
  near(r.stats.feedMinutes, (0.3 * 25.4) / 254 + 25.4 / 508, 1e-9);
  near(r.stats.zMin, -2.54, 1e-6);
});

test('comments in () and ; are ignored, header is read', () => {
  const r = parseGcode('(TITLE Job (A) sheet 1)\n(TOOL 6 mm flat D6 FLAT - load this bit)\n'
    + '(SHEET 1220 x 610 mm, origin bottom-left, Z0 = material top)\n(DEPTH Z-3.3)\n'
    + 'G21 G90 G17 ; metric\nG0 Z5 (safe) X0\nM03 S18000\nG1 Z-1 F800 ; plunge X999\nM30\n');
  assert.equal(r.header.tool, '6 mm flat');
  assert.equal(r.header.toolD, 6);
  assert.deepEqual(r.sheet, { x0: 0, y0: 0, w: 1220, h: 610 });
  assert.equal(r.expectZ, -3.3);
  near(r.stats.cutMm, 6, 1e-9);           // X999 after ';' is a comment
  assert.deepEqual(codes(r), []);
  const s = parseGcode('(SIZE X0..200 Y0..150 mm, origin bottom-left)\nM30');
  assert.deepEqual(s.sheet, { x0: 0, y0: 0, w: 200, h: 150 });
});

test('warnings: too deep, outside the sheet, cutting in the air, missing M30', () => {
  const r = parseGcode('(SHEET 100 x 50 mm)\nG21 G90\nM3 S1000\nG0 Z5\nG0 X10 Y10\nG1 Z-4 F500\nX120\nG0 Z5\nG1 X10 F1000\n', { expectZ: -3.3 });
  const c = codes(r);
  for (const k of ['too_deep', 'outside', 'air_cut', 'no_m30']) assert.ok(c.includes(k), `${k} in ${c}`);
  const deep = r.warnings.find((w) => w.code === 'too_deep');
  assert.deepEqual(deep.lines, [6, 7]);
  near(deep.deepest, -4, 1e-6);
  assert.deepEqual(r.warnings.find((w) => w.code === 'outside').lines, [7, 8]);   // the lift at X120 too
  assert.deepEqual(r.warnings.find((w) => w.code === 'air_cut').lines, [9]);
  // M06 and a move after M30
  const t = parseGcode('M3\nM6 T2\nG0 X1 Z1\nM30\nG0 X5\n');
  assert.ok(codes(t).includes('tool_change') && codes(t).includes('after_end'));
});

test('depth colour levels: one per pass when few, bands when many', () => {
  const r = parseGcode('M3\nG0 X0 Y0 Z5\nG1 Z-1 F500\nX10\nG1 Z-2\nX0\nG1 Z-3\nX10\nG0 Z5\nM30');
  assert.equal(r.levels.discrete, true);
  assert.deepEqual(r.levels.values, [-1, -2, -3]);
  const binsOfCuts = [...r.bin].filter((b, i) => r.kind[i] !== 0);
  assert.deepEqual([...new Set(binsOfCuts)], [0, 1, 2]);
  let g = 'M3\nG0 X0 Y0 Z0\n';
  for (let i = 0; i < 50; i++) g += `G1 X${i} Z${-i / 10} F1000\n`;
  const many = parseGcode(g + 'M30');
  assert.equal(many.levels.discrete, false);
  assert.equal(many.levels.values.length, 8);
});

test('chunked parsing gives the same result as one string, any split', () => {
  const text = fs.readFileSync(path.join(root, 'tests_js', 'tmp-sample.nc'), 'utf8');
  const one = parseGcode(text);
  for (const size of [1, 7, 4096]) {
    const p = createParser();
    for (let i = 0; i < text.length; i += size) p.push(text.slice(i, i + size));
    const r = p.end();
    assert.equal(r.n, one.n);
    assert.equal(r.stats.lines, one.stats.lines);
    near(r.stats.minutes, one.stats.minutes, 1e-9);
  }
  // line offsets point at the start of each line
  const lines = text.split('\n');
  assert.equal(text.slice(one.lineStart[2], one.lineStart[3]).replace(/\r?\n$/, ''), lines[2].replace(/\r$/, ''));
});

test('time lookup: segmentAt', () => {
  const r = parseGcode('M3\nG0 X0 Y0 Z0\nG1 X10 F600\nX20\nX30\nM30');
  assert.equal(segmentAt(r.tcum, 0), 0);
  assert.equal(segmentAt(r.tcum, 1 / 60 + 1e-9), 1);
  assert.equal(segmentAt(r.tcum, 1), r.n);
});

test('reads cam/ncstudio.py output back: time within 1 %, lines, depth, no warnings', () => {
  if (!info) { console.log('  (python not found, skipped)'); return; }
  const r = parseGcode(fs.readFileSync(tmp, 'utf8'));
  assert.ok(Math.abs(r.stats.minutes - info.minutes) / info.minutes < 0.01, `sim ${r.stats.minutes} vs writer ${info.minutes}`);
  near(r.stats.cutMm, info.cut, info.cut * 0.001, 'cut length');
  assert.equal(r.stats.lines, info.lines);
  near(r.stats.zMin, info.z_min, 1e-6);
  assert.deepEqual(r.sheet, { x0: 0, y0: 0, w: 1220, h: 610 });
  assert.deepEqual(codes(r), []);
  assert.deepEqual(r.levels.values, [-1.1, -2.2, -3.3]);
});

test('known value: hand-computed job time', () => {
  // 100 x 50 rectangle at two levels with 6000 mm/min rapids:
  //   rapids: (0,0)->(10,10), lift 8 mm, back to (0,0); plunges 6.5 + 1.5 mm at F800;
  //   cuts: 2 x 300 mm at F2000. G4 dwell is not counted (same as the writer).
  const g = ['G21 G90 G17', 'G0 Z5', 'M03 S18000', 'G4 P3', 'G0 X10 Y10', 'G1 Z-1.5 F800',
    'G1 X110 Y10 Z-1.5 F2000', 'X110 Y60 Z-1.5', 'X10 Y60 Z-1.5', 'X10 Y10 Z-1.5', 'G1 Z-3 F800',
    'G1 X110 Y10 Z-3 F2000', 'X110 Y60 Z-3', 'X10 Y60 Z-3', 'X10 Y10 Z-3', 'G0 Z5', 'M05', 'G0 X0 Y0', 'M30'].join('\r\n');
  const r = parseGcode(g);
  const expected = (Math.hypot(10, 10) * 2 + 8) / 6000 + (6.5 + 1.5) / 800 + 600 / 2000;   // 0.31605 min
  assert.ok(Math.abs(r.stats.minutes - expected) / expected < 0.01, `${r.stats.minutes} vs ${expected}`);
  near(r.stats.minutes, 0.3160474, 1e-6);
});

test('300k lines parse fast', () => {
  let g = '(SIZE X0..300 Y0..300 mm)\nG21 G90\nM3 S18000\nG0 Z5\nG0 X0 Y0\nG1 Z-1 F800\n';
  const parts = [];
  for (let i = 0; i < 300000; i++) {
    const row = Math.floor(i / 1000), col = i % 1000;
    const x = row % 2 ? 300 - col * 0.3 : col * 0.3;
    parts.push(`X${x.toFixed(3)} Y${(row * 1).toFixed(3)} Z${(-1 - Math.sin(i / 300)).toFixed(3)}`);
  }
  g += parts.join('\n') + '\nG0 Z5\nM30\n';
  const t0 = Date.now();
  const r = parseGcode(g);
  const ms = Date.now() - t0;
  console.log(`  300k lines (${(g.length / 1e6).toFixed(1)} MB) parsed in ${ms} ms, ${r.n} segments`);
  assert.ok(r.stats.lines > 300000);
  assert.ok(ms < 5000, `too slow: ${ms} ms`);
});

fs.rmSync(path.join(root, 'tests_js', 'tmp-sample.nc'), { force: true });
fs.rmSync(tmp, { force: true });
console.log(`\n${passed}/${passed + failed} tests passed`);
process.exit(failed ? 1 : 0);
