// G-code reader for the simulator (PLAN.md Part 2, item 2.7).
// Plain ES module with no DOM use, so it runs in the page, in a Web Worker and in node.
//
// Understands what our writers produce and the common NC Studio words:
//   G0 G1 G2 G3 (arcs by I/J or R, helical Z), G4 (dwell, skipped), G17, G20/G21,
//   G90/G91, G90.1/G91.1, F, S, M3/M4/M5, M30/M2, comments in () and after ;.
// Arcs are split into short straight segments for drawing; lengths and times use
// the exact arc. Time estimate is the same idea as cam/ncstudio.py:
//   feed moves: length / F, rapid moves: length / 6000 mm/min.
//
// Output (all moves as flat typed arrays, so 300k-line files stay small):
//   n            number of segments
//   px, py, pz   Float32Array(n + 1): point i is the start of segment i, point i+1 its end
//   kind         Uint8Array(n): 0 rapid, 1 feed line, 2 feed arc
//   bin          Uint8Array(n): depth colour index (255 = cut above Z0)
//   line         Uint32Array(n): source line number (1-based)
//   feed         Float32Array(n): F in mm/min (rapid speed for G0)
//   tcum         Float64Array(n): minutes from the start to the end of segment i
//   lineStart    Uint32Array(lines + 1): character offset of each source line
//   stats, header, warnings, levels

export const RAPID_MM_MIN = 6000;          // same as cam/ncstudio.py and relief/gcode.py
const EPS = 1e-6;
const ARC_TOL_MM = 0.02;                   // drawing only: max chord error of arc segments
const MAX_WARN_LINES = 5;
const HEADER_LINES = 40;                   // header comments are read from the first lines only                  // remember the first few line numbers per warning

// Growable typed array helper.
class Grow {
  constructor(Type, cap = 4096) { this.T = Type; this.a = new Type(cap); this.n = 0; }
  push(v) {
    if (this.n === this.a.length) { const b = new this.T(this.a.length * 2); b.set(this.a); this.a = b; }
    this.a[this.n++] = v;
  }
  done() { return this.a.slice(0, this.n); }
}

// Header comments: (TITLE ..), (TOOL name D6 FLAT ..), (SHEET 2440 x 1220 mm ..),
// (SIZE X0..200 Y0..150 mm ..), optional (DEPTH Z-3.3) / (ZMIN -3.3) / (MATERIAL 3 mm).
export function readHeaderComment(c, header) {
  let m;
  const s = c.trim();
  if ((m = s.match(/^TITLE\s+(.*)$/i))) header.title = m[1].trim();
  else if ((m = s.match(/^TOOL\s+(.*?)\s+D([\d.]+)/i))) { header.tool = m[1].trim(); header.toolD = Number(m[2]); }
  else if ((m = s.match(/^SHEET\s+([\d.]+)\s*x\s*([\d.]+)\s*mm/i))) header.sheet = { x0: 0, y0: 0, w: Number(m[1]), h: Number(m[2]) };
  else if ((m = s.match(/^SIZE\s+X(-?[\d.]+)\.\.(-?[\d.]+)\s+Y(-?[\d.]+)\.\.(-?[\d.]+)/i))) {
    const x0 = Number(m[1]), x1 = Number(m[2]), y0 = Number(m[3]), y1 = Number(m[4]);
    header.sheet = { x0, y0, w: x1 - x0, h: y1 - y0 };
  } else if ((m = s.match(/^(?:DEPTH|ZMIN|DEEPEST)\b[^-\d]*(-?[\d.]+)/i))) header.expectZ = -Math.abs(Number(m[1]));
  else if ((m = s.match(/^(?:MATERIAL|THICKNESS)\b[^\d]*([\d.]+)\s*mm/i)) && header.expectZ === undefined) {
    header.materialMm = Number(m[1]);
  }
}

// Create a chunked parser: push(text) any number of times (chunks may split lines),
// then end() returns the result. opts: { expectZ, sheet: {x0,y0,w,h}, onProgress }.
export function createParser(opts = {}) {
  const px = new Grow(Float32Array), py = new Grow(Float32Array), pz = new Grow(Float32Array);
  const kind = new Grow(Uint8Array), lineNo = new Grow(Uint32Array), feedA = new Grow(Float32Array);
  const tcum = new Grow(Float64Array), lineStart = new Grow(Uint32Array);
  const header = {};
  const warn = new Map();   // code -> {code, count, lines: [], ...extra}
  const addWarn = (code, ln, extra) => {
    let w = warn.get(code);
    if (!w) { w = { code, count: 0, lines: [], ...extra }; warn.set(code, w); }
    if (w.lastLine === ln) return;          // count each source line once
    w.lastLine = ln;
    w.count++;
    if (w.lines.length < MAX_WARN_LINES) w.lines.push(ln);
  };

  // modal state
  let motion = 0;            // 0 1 2 3
  let absolute = true, arcAbs = false, inch = false;
  let F = 0, S = 0, spindle = false, ended = false, plane = 17;
  let x = 0, y = 0, z = NaN; // z unknown until the first Z word (first G0 Z5 has no length)
  let started = false;       // point 0 written
  let t = 0;                 // minutes
  let cut = 0, rapid = 0, plungeLen = 0, feedTime = 0, rapidTime = 0;
  let zMin = Infinity, zMax = -Infinity, cutZMin = Infinity, cutZMax = -Infinity;
  let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
  let cbx0 = Infinity, cby0 = Infinity, cbx1 = -Infinity, cby1 = -Infinity;
  let lines = 0, offset = 0, carry = '';
  let sawEnd = false, motionAfterEnd = false;

  const ctx = { sheet: opts.sheet || null, expectZ: Number.isFinite(opts.expectZ) ? opts.expectZ : undefined };

  function startPoint() {
    if (started) return;
    started = true;
    px.push(x); py.push(y); pz.push(Number.isFinite(z) ? z : 0);
  }

  function checkPoint(nx, ny, ln) {
    if (nx < bx0) bx0 = nx; if (nx > bx1) bx1 = nx;
    if (ny < by0) by0 = ny; if (ny > by1) by1 = ny;
    const sh = ctx.sheet;
    if (sh && (nx < sh.x0 - 0.01 || ny < sh.y0 - 0.01 || nx > sh.x0 + sh.w + 0.01 || ny > sh.y0 + sh.h + 0.01)) {
      addWarn('outside', ln);
    }
  }

  // one straight segment to (nx, ny, nz); k = 0 rapid, 1 line, 2 arc piece; dt minutes
  function seg(nx, ny, nz, k, f, ln, dt) {
    startPoint();
    px.push(nx); py.push(ny); pz.push(nz);
    kind.push(k); lineNo.push(ln); feedA.push(f);
    t += dt; tcum.push(t);
    checkPoint(nx, ny, ln);
  }

  function zStats(z0, z1, isFeed) {
    const lo = Math.min(z0, z1), hi = Math.max(z0, z1);
    if (lo < zMin) zMin = lo; if (hi > zMax) zMax = hi;
    if (isFeed) {
      if (lo < cutZMin) cutZMin = lo; if (hi > cutZMax) cutZMax = hi;
    }
  }

  function feedChecks(x0, y0, z0, x1, y1, z1, ln) {
    if (F <= 0) addWarn('no_feed', ln);
    if (!spindle && Math.min(z0, z1) < -EPS) addWarn('spindle_off', ln);
    if (Math.min(z0, z1) > 0.001 && Math.hypot(x1 - x0, y1 - y0) > EPS) addWarn('air_cut', ln);
    if (ctx.expectZ !== undefined && Math.min(z0, z1) < ctx.expectZ - 0.005) {
      addWarn('too_deep', ln, { expect: ctx.expectZ });
      const w = warn.get('too_deep');
      w.deepest = Math.min(w.deepest ?? 0, z0, z1);
    }
    const lo = Math.min(z0, z1);
    if (lo <= 0.001) {
      if (x0 < cbx0) cbx0 = x0; if (x0 > cbx1) cbx1 = x0; if (y0 < cby0) cby0 = y0; if (y0 > cby1) cby1 = y0;
      if (x1 < cbx0) cbx0 = x1; if (x1 > cbx1) cbx1 = x1; if (y1 < cby0) cby0 = y1; if (y1 > cby1) cby1 = y1;
    }
  }

  function lineMove(nx, ny, nz, rapidMove, ln) {
    const z0 = Number.isFinite(z) ? z : nz;
    if (!Number.isFinite(z)) { z = nz; if (!started) { startPoint(); pz.a[0] = nz; } }
    const dx = nx - x, dy = ny - y, dz = nz - z0;
    const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (len < EPS) { x = nx; y = ny; z = nz; return; }
    if (rapidMove) {
      rapid += len;
      const dt = len / RAPID_MM_MIN;
      rapidTime += dt;
      zStats(z0, nz, false);
      seg(nx, ny, nz, 0, RAPID_MM_MIN, ln, dt);
    } else {
      feedChecks(x, y, z0, nx, ny, nz, ln);
      const f = F > 0 ? F : 1000;
      const dt = len / f;
      if (Math.hypot(dx, dy) <= EPS) plungeLen += len; else cut += len;
      feedTime += dt;
      zStats(z0, nz, true);
      seg(nx, ny, nz, 1, F, ln, dt);
    }
    x = nx; y = ny; z = nz;
  }

  function arcMove(nx, ny, nz, cw, w, ln) {
    if (plane !== 17) addWarn('plane', ln);
    const z0 = Number.isFinite(z) ? z : nz;
    if (!Number.isFinite(z)) { z = nz; if (!started) { startPoint(); pz.a[0] = nz; } }
    let i, j;
    if (w.R !== undefined) {
      // radius format (same maths as grbl)
      const dx = nx - x, dy = ny - y;
      let r = w.R;
      const d2 = dx * dx + dy * dy;
      if (d2 < EPS * EPS) { addWarn('bad_arc', ln); x = nx; y = ny; z = nz; return; }
      let h = 4 * r * r - d2;
      if (h < 0) { if (h < -0.01 * 4 * r * r - 1e-4) addWarn('bad_arc', ln); h = 0; }
      let hd = -Math.sqrt(h) / Math.sqrt(d2);
      if (!cw) hd = -hd;
      if (r < 0) { hd = -hd; r = -r; }
      i = 0.5 * (dx - dy * hd);
      j = 0.5 * (dy + dx * hd);
    } else {
      i = w.I ?? 0; j = w.J ?? 0;
      if (arcAbs) { i -= x; j -= y; }
    }
    const cx = x + i, cy = y + j;
    const r0 = Math.hypot(x - cx, y - cy), r1 = Math.hypot(nx - cx, ny - cy);
    if (r0 < EPS) { addWarn('bad_arc', ln); lineMove(nx, ny, nz, false, ln); return; }
    if (Math.abs(r0 - r1) > 0.01 + 0.001 * r0) addWarn('bad_arc', ln);
    const a0 = Math.atan2(y - cy, x - cx), a1 = Math.atan2(ny - cy, nx - cx);
    let sweep = cw ? a0 - a1 : a1 - a0;
    while (sweep < 0) sweep += 2 * Math.PI;
    if (sweep < 1e-9) sweep = 2 * Math.PI;          // same start and end: full circle
    const dz = nz - z0;
    const len = Math.hypot(r0 * sweep, dz);
    feedChecks(x, y, z0, nx, ny, nz, ln);
    const f = F > 0 ? F : 1000;
    const dtAll = len / f;
    cut += len; feedTime += dtAll;
    zStats(z0, nz, true);
    const step = r0 > ARC_TOL_MM ? 2 * Math.acos(1 - ARC_TOL_MM / r0) : Math.PI / 4;
    const nseg = Math.max(1, Math.min(720, Math.ceil(sweep / step)));
    const dir = cw ? -1 : 1;
    for (let k = 1; k <= nseg; k++) {
      const u = k / nseg;
      let qx, qy;
      if (k === nseg) { qx = nx; qy = ny; } else {
        const a = a0 + dir * sweep * u;
        qx = cx + r0 * Math.cos(a); qy = cy + r0 * Math.sin(a);
      }
      seg(qx, qy, z0 + dz * u, 2, F, ln, dtAll / nseg);
    }
    // the bounding box of the cut must include the arc, not only its end points
    for (let k = 0; k < 4; k++) {
      const a = k * Math.PI / 2;
      let rel = (cw ? a0 - a : a - a0); while (rel < 0) rel += 2 * Math.PI;
      if (rel <= sweep) {
        const qx = cx + r0 * Math.cos(a), qy = cy + r0 * Math.sin(a);
        if (Math.min(z0, nz) <= 0.001) {
          if (qx < cbx0) cbx0 = qx; if (qx > cbx1) cbx1 = qx; if (qy < cby0) cby0 = qy; if (qy > cby1) cby1 = qy;
        }
      }
    }
    x = nx; y = ny; z = nz;
  }

  // Parse one source line (without the newline). ln is 1-based.
  function doLine(s, ln) {
    const n = s.length;
    const w = {};
    let any = false, hasXYZ = false, dwell = false;
    const gs = [];
    const ms = [];
    let i = 0;
    while (i < n) {
      const c = s.charCodeAt(i);
      if (c === 40) {                        // '(' comment
        const e = s.indexOf(')', i + 1);
        const txt = s.slice(i + 1, e < 0 ? n : e);
        if (ln <= HEADER_LINES) {
          readHeaderComment(txt, header);
          if (!ctx.sheet && header.sheet) ctx.sheet = header.sheet;
          if (ctx.expectZ === undefined && header.expectZ !== undefined) ctx.expectZ = header.expectZ;
        }
        i = e < 0 ? n : e + 1;
        continue;
      }
      if (c === 59) break;                   // ';' comment to end of line
      let L = c | 32;                         // lower-case letter
      if (L < 97 || L > 122) { i++; continue; }
      i++;
      while (i < n && s.charCodeAt(i) === 32) i++;
      // number
      let j = i, neg = false;
      let cc = s.charCodeAt(j);
      if (cc === 45 || cc === 43) { neg = cc === 45; j++; }
      let v = 0, digits = 0, frac = 0, scale = 1;
      while (j < n) {
        cc = s.charCodeAt(j);
        if (cc >= 48 && cc <= 57) {
          if (frac) { scale /= 10; v += (cc - 48) * scale; } else v = v * 10 + (cc - 48);
          digits++; j++;
        } else if (cc === 46 && !frac) { frac = 1; j++; } else break;
      }
      if (!digits) { i = j; continue; }
      if (neg) v = -v;
      i = j;
      any = true;
      switch (L) {
        case 103: gs.push(v); break;            // g
        case 109: ms.push(v); break;            // m
        case 120: w.X = v; hasXYZ = true; break;
        case 121: w.Y = v; hasXYZ = true; break;
        case 122: w.Z = v; hasXYZ = true; break;
        case 105: w.I = v; break;
        case 106: w.J = v; break;
        case 114: w.R = v; break;
        case 102: w.F = v; break;
        case 115: w.S = v; break;
        case 112: w.P = v; break;
        default: break;                         // N, T, K ... ignored
      }
    }
    if (!any) return;
    for (const g of gs) {
      const gi = Math.round(g * 10);
      if (gi === 0 || gi === 10 || gi === 20 || gi === 30) motion = gi / 10;
      else if (gi === 40) dwell = true;
      else if (gi === 170) plane = 17;
      else if (gi === 180 || gi === 190) { plane = gi / 10; addWarn('plane', ln); }
      else if (gi === 200) inch = true;
      else if (gi === 210) inch = false;
      else if (gi === 900) absolute = true;
      else if (gi === 910) absolute = false;
      else if (gi === 901) arcAbs = true;
      else if (gi === 911) arcAbs = false;
      else if (gi === 940 || gi === 540 || gi === 400 || gi === 490 || gi === 800 || gi === 610 || gi === 640) { /* harmless */ }
      else addWarn('unsupported', ln, { word: `G${g}` });
    }
    const k = inch ? 25.4 : 1;
    if (w.F !== undefined) F = w.F * k;
    if (w.S !== undefined) S = w.S;
    for (const m of ms) {
      if (m === 3 || m === 4) spindle = true;
      else if (m === 5) spindle = false;
      else if (m === 30 || m === 2) { sawEnd = true; ended = true; }
      else if (m === 6) addWarn('tool_change', ln);
    }
    if (dwell || !hasXYZ) return;
    if (ended) { motionAfterEnd = true; addWarn('after_end', ln); return; }
    const cz = Number.isFinite(z) ? z : 0;
    let nx = x, ny = y, nz = Number.isFinite(z) ? z : NaN;
    if (absolute) {
      if (w.X !== undefined) nx = w.X * k;
      if (w.Y !== undefined) ny = w.Y * k;
      if (w.Z !== undefined) nz = w.Z * k;
    } else {
      if (w.X !== undefined) nx = x + w.X * k;
      if (w.Y !== undefined) ny = y + w.Y * k;
      if (w.Z !== undefined) nz = cz + w.Z * k;
    }
    if (!Number.isFinite(nz)) nz = 0;  // XY move before any Z word: assume Z0
    const ww = { I: w.I !== undefined ? w.I * k : undefined, J: w.J !== undefined ? w.J * k : undefined,
      R: w.R !== undefined ? w.R * k : undefined };
    if (motion === 2 || motion === 3) arcMove(nx, ny, nz, motion === 2, ww, ln);
    else lineMove(nx, ny, nz, motion === 0, ln);
  }

  function processText(text, final) {
    const s = carry + text;
    let p = 0;
    const n = s.length;
    while (true) {
      const e = s.indexOf('\n', p);
      if (e < 0) break;
      lines++;
      lineStart.push(offset + p);
      let q = e;
      if (q > p && s.charCodeAt(q - 1) === 13) q--;
      doLine(s.slice(p, q), lines);
      p = e + 1;
    }
    offset += p;
    carry = s.slice(p);
    if (final && carry.length) {
      lines++;
      lineStart.push(offset);
      const q = carry.charCodeAt(carry.length - 1) === 13 ? carry.length - 1 : carry.length;
      doLine(carry.slice(0, q), lines);
      offset += carry.length;
      carry = '';
    }
    if (n && opts.onProgress) opts.onProgress(offset);
  }

  return {
    // The sheet and expected depth can be set before parsing starts: the header
    // comments come first, so a caller may also let the header decide.
    push(text) {
      processText(text, false);
      if (!ctx.sheet && header.sheet) ctx.sheet = header.sheet;
      if (ctx.expectZ === undefined && header.expectZ !== undefined) ctx.expectZ = header.expectZ;
    },
    get lines() { return lines; },
    end() {
      processText('', true);
      if (!ctx.sheet && header.sheet) ctx.sheet = header.sheet;
      startPoint();
      lineStart.push(offset);
      if (!sawEnd) addWarn('no_m30', lines, {});
      if (ctx.expectZ === undefined && header.expectZ !== undefined) ctx.expectZ = header.expectZ;
      const n = kind.n;
      const res = {
        n,
        px: px.done(), py: py.done(), pz: pz.done(),
        kind: kind.done(), line: lineNo.done(), feed: feedA.done(), tcum: tcum.done(),
        lineStart: lineStart.done(),
        header,
        sheet: ctx.sheet,
        expectZ: ctx.expectZ,
        stats: {
          lines, moves: n,
          cutMm: cut + plungeLen, xyCutMm: cut, plungeMm: plungeLen, rapidMm: rapid,
          feedMinutes: feedTime, rapidMinutes: rapidTime, minutes: feedTime + rapidTime,
          zMin: Number.isFinite(zMin) ? zMin : 0, zMax: Number.isFinite(zMax) ? zMax : 0,
          cutZMin: Number.isFinite(cutZMin) ? cutZMin : 0, cutZMax: Number.isFinite(cutZMax) ? cutZMax : 0,
          bounds: Number.isFinite(bx0) ? { x0: bx0, y0: by0, x1: bx1, y1: by1 } : null,
          cutBounds: Number.isFinite(cbx0) ? { x0: cbx0, y0: cby0, x1: cbx1, y1: cby1 } : null,
          lastS: S, endedWithM30: sawEnd, motionAfterEnd,
        },
        warnings: [...warn.values()].map(({ lastLine, ...w }) => w),
      };
      const lv = depthLevels(res);
      res.levels = lv.levels;
      res.bin = lv.bin;
      return res;
    },
  };
}

// Colour bins by depth. If the cuts use at most 8 distinct Z levels (our 2D files:
// one per pass, plus tabs and marks) each level gets its own colour; otherwise
// 8 even bands between the deepest cut and Z0. bin 255 = feed move above Z0.
export function depthLevels(r) {
  const { n, pz, kind } = r;
  const bin = new Uint8Array(n);
  const uniq = new Map();
  let discrete = true;
  for (let i = 0; i < n && discrete; i++) {
    if (kind[i] === 0) continue;
    const zl = Math.min(pz[i], pz[i + 1]);
    if (zl > 0.001) continue;
    const key = Math.round(zl * 100);
    if (!uniq.has(key)) { uniq.set(key, zl); if (uniq.size > 8) discrete = false; }
  }
  let levels;
  if (discrete) {
    const keys = [...uniq.keys()].sort((a, b) => b - a);    // shallow -> deep
    const idx = new Map(keys.map((k, i) => [k, i]));
    levels = { discrete: true, values: keys.map((k) => k / 100) };
    for (let i = 0; i < n; i++) {
      if (kind[i] === 0) continue;
      const zl = Math.min(pz[i], pz[i + 1]);
      bin[i] = zl > 0.001 ? 255 : idx.get(Math.round(zl * 100));
    }
  } else {
    const lo = r.stats.cutZMin, hi = Math.min(0, r.stats.cutZMax);
    const span = hi - lo || 1;
    const N = 8;
    levels = { discrete: false, values: Array.from({ length: N }, (_, k) => hi - (span * (k + 0.5)) / N), lo, hi };
    for (let i = 0; i < n; i++) {
      if (kind[i] === 0) continue;
      const zl = Math.min(pz[i], pz[i + 1]);
      if (zl > 0.001) { bin[i] = 255; continue; }
      bin[i] = Math.max(0, Math.min(N - 1, Math.floor(((hi - zl) / span) * N)));
    }
  }
  return { levels, bin };
}

// Convenience: parse a whole string at once.
export function parseGcode(text, opts = {}) {
  const p = createParser(opts);
  p.push(text);
  return p.end();
}

// Index of the segment running at time tMin (minutes): the first i with tcum[i] >= t.
export function segmentAt(tcum, tMin) {
  let lo = 0, hi = tcum.length - 1;
  if (hi < 0) return 0;
  if (tMin >= tcum[hi]) return hi + 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tcum[mid] < tMin) lo = mid + 1; else hi = mid;
  }
  return lo;
}

// Typed arrays of a result, for postMessage transfer lists.
export function transferList(r) {
  return [r.px, r.py, r.pz, r.kind, r.bin, r.line, r.feed, r.tcum, r.lineStart].map((a) => a.buffer);
}
