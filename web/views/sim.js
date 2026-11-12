// G-code simulator overlay (PLAN.md Part 2, item 2.7).
// openSimulator({ url, name, expectZ, sheet }) fetches an .nc file, parses it in a
// Web Worker (web/sim_worker.js, falls back to chunks on the main thread) and plays
// it back on a 2D canvas: top view (or a simple tilted 3D view), rapid moves dashed,
// cut moves coloured by depth, play / pause / speed / scrub / step, live X Y Z F readout,
// totals and warnings. Three stacked canvases keep it fast on big relief files:
//   bg   = sheet + the whole path in grey (drawn once per view)
//   prog = moves done so far (drawn incrementally while playing)
//   top  = the bit and the part of the current move (redrawn every frame)
import { t } from '../i18n.js';
import { h, icon, num, fill } from '../ui.js';
import { createParser } from '../sim_parse.js';

const SPEEDS = [1, 2, 5, 10, 20, 50];
const DEPTH_COLOURS = ['#8fd14f', '#3fbf7f', '#16a39a', '#1f84a8', '#3366b8', '#4a46a8', '#62278f', '#5a0f6a'];
const AIR_COLOUR = '#f08c00';
const RAPID_COLOUR = '#e5534b';
const SLIDER_MAX = 10000;

let openNow = null;   // only one simulator at a time

export function openSimulator({ url, name, expectZ, sheet } = {}) {
  if (openNow) openNow.close();
  const sim = new Simulator({ url, name, expectZ, sheet });
  openNow = sim;
  return sim;
}

class Simulator {
  constructor(o) {
    this.o = o;
    this.R = null;                // parse result
    this.text = '';
    this.mode = 'top';
    this.view = { s: 1, ox: 0, oy: 0 };      // world -> screen: X = qx*s + ox, Y = -qy*s + oy
    this.rv = { s: 1, ox: 0, oy: 0 };        // view the bg/prog canvases were drawn with
    this.simT = 0; this.total = 0;
    this.playing = false; this.speed = 10;
    this.drawn = 0;
    this.raf = 0; this.lastTs = 0;
    this.closed = false;
    this.worker = null;
    this.build();
    this.load().catch((e) => this.fail(e));
  }

  // ---------------------------------------------------------------- DOM
  build() {
    const c = (cls) => h('canvas', { class: `sim-cv ${cls}` });
    this.bg = c('bg'); this.prog = c('prog');
    this.top = h('canvas', { class: 'sim-cv top', testid: 'sim-canvas', tabindex: '0', 'aria-label': t('sim_title') });
    this.msg = h('div', { class: 'sim-msg' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', {}, t('sim_loading')));
    this.legend = h('div', { class: 'sim-legend', testid: 'sim-legend' });
    this.fitBtn = h('button', { class: 'tbtn', title: t('sim_fit'), 'aria-label': t('sim_fit'), onclick: () => this.fit() }, icon('fit', 18));
    this.viewBtn = h('button', { class: 'tbtn', testid: 'sim-view', onclick: () => this.toggleMode() }, t('sim_view_3d'));
    this.stage = h('div', { class: 'sim-stage' }, this.bg, this.prog, this.top, this.msg, this.legend,
      h('div', { class: 'sim-stage-ctrls' }, this.viewBtn, this.fitBtn));

    this.playBtn = h('button', { class: 'btn primary sim-play', testid: 'sim-play', disabled: true, onclick: () => this.togglePlay() },
      icon('play', 18), h('span', {}, t('sim_play')));
    this.prevBtn = h('button', { class: 'btn icon', testid: 'sim-prev', title: t('sim_prev'), 'aria-label': t('sim_prev'), disabled: true, onclick: () => this.step(-1) }, icon('prev', 18));
    this.nextBtn = h('button', { class: 'btn icon', testid: 'sim-next', title: t('sim_next'), 'aria-label': t('sim_next'), disabled: true, onclick: () => this.step(1) }, icon('next', 18));
    this.slider = h('input', { type: 'range', class: 'sim-slider', testid: 'sim-slider', min: '0', max: String(SLIDER_MAX), step: '1', value: '0', disabled: true, 'aria-label': t('sim_scrub') });
    this.slider.addEventListener('input', () => { this.setTime((Number(this.slider.value) / SLIDER_MAX) * this.total); });
    this.speedSel = h('select', { class: 'sim-speed', testid: 'sim-speed', 'aria-label': t('sim_speed') },
      SPEEDS.map((s) => h('option', { value: String(s), selected: s === this.speed }, `${s}×`)));
    this.speedSel.addEventListener('change', () => { this.speed = Number(this.speedSel.value) || 1; });
    this.timeEl = h('span', { class: 'sim-time', testid: 'sim-time' }, '0:00 / 0:00');
    this.pos = h('div', { class: 'sim-pos', testid: 'sim-pos' });
    this.code = h('code', { class: 'sim-code', testid: 'sim-line' }, '');

    this.stats = h('div', { class: 'sim-stats', testid: 'sim-stats' });
    this.warns = h('div', { class: 'sim-warnings', testid: 'sim-warnings' });

    const closeBtn = h('button', { class: 'btn', testid: 'sim-close', onclick: () => this.close() }, t('close'));
    this.el = h('div', { class: 'sim', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('sim_title'), testid: 'sim' },
      h('header', { class: 'sim-bar' },
        h('strong', { class: 'sim-name' }, icon('play', 18), h('span', {}, this.o.name || t('sim_title'))),
        h('span', { class: 'muted small sim-tool' }), h('span', { class: 'grow' }), closeBtn),
      h('div', { class: 'sim-main' },
        h('section', { class: 'sim-center' }, this.stage,
          h('div', { class: 'sim-ctrl' }, this.prevBtn, this.playBtn, this.nextBtn, this.slider,
            h('label', { class: 'sim-speed-l' }, h('span', { class: 'muted small' }, t('sim_speed')), this.speedSel), this.timeEl),
          h('div', { class: 'sim-read' }, this.pos, this.code)),
        h('aside', { class: 'sim-side' },
          h('h3', {}, t('sim_totals')), this.stats,
          h('h3', {}, t('sim_warnings')), this.warns)));
    document.body.append(this.el);
    document.body.classList.add('sim-open');

    this.onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); this.close(); return; }
      if (!this.R || e.target.closest?.('input,select,textarea')) return;
      if (e.key === ' ') { e.preventDefault(); this.togglePlay(); } else if (e.key === 'ArrowRight') { e.preventDefault(); this.step(1); } else if (e.key === 'ArrowLeft') { e.preventDefault(); this.step(-1); } else if (e.key === 'Home') this.setTime(0);
      else if (e.key === 'End') this.setTime(this.total);
      else if (e.key === '0') this.fit();
    };
    document.addEventListener('keydown', this.onKey);
    this.ro = new ResizeObserver(() => { if (this.R) this.resize(!this.userMoved); });
    this.ro.observe(this.stage);
    this.bindPointer();
    this.top.focus({ preventScroll: true });
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.playing = false;
    cancelAnimationFrame(this.raf);
    clearTimeout(this.fullTimer);
    if (this.worker) this.worker.terminate();
    this.ro.disconnect();
    document.removeEventListener('keydown', this.onKey);
    this.el.remove();
    document.body.classList.remove('sim-open');
    if (openNow === this) openNow = null;
  }

  fail(e) {
    if (this.closed) return;
    fill(this.msg, h('span', { class: 'sim-err' }, `${t('sim_err_load')}: ${e?.message || e}`));
    this.msg.classList.remove('hidden');
  }

  setMsg(text) { fill(this.msg, h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', {}, text)); }

  // ---------------------------------------------------------------- loading
  async load() {
    const res = await fetch(this.o.url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    this.text = await res.text();
    if (this.closed) return;
    this.setMsg(t('sim_parsing', { p: 0 }));
    const opts = {};
    if (Number.isFinite(this.o.expectZ)) opts.expectZ = this.o.expectZ;
    if (this.o.sheet) opts.sheet = this.o.sheet;
    let R;
    try { R = await this.parseInWorker(opts); } catch { R = await this.parseHere(opts); }
    if (this.closed) return;
    this.R = R;
    this.total = R.n ? R.tcum[R.n - 1] : 0;
    this.msg.classList.add('hidden');
    for (const b of [this.playBtn, this.prevBtn, this.nextBtn, this.slider]) b.disabled = !R.n;
    const tool = this.el.querySelector('.sim-tool');
    tool.textContent = [R.header.tool, R.header.toolD ? `Ø ${num(R.header.toolD, 2)} mm` : null].filter(Boolean).join(' · ');
    this.renderStats();
    this.renderWarnings();
    this.renderLegend();
    this.project();
    this.resize(true);
    this.updateReadout();
  }

  parseInWorker(opts) {
    return new Promise((resolve, reject) => {
      let w;
      try { w = new Worker(new URL('../sim_worker.js', import.meta.url), { type: 'module' }); } catch (e) { reject(e); return; }
      this.worker = w;
      w.onmessage = (e) => {
        const m = e.data;
        if (m.type === 'progress') this.setMsg(t('sim_parsing', { p: Math.round((m.done / Math.max(1, m.total)) * 100) }));
        else if (m.type === 'done') { w.terminate(); this.worker = null; resolve(m.result); } else if (m.type === 'error') { w.terminate(); this.worker = null; reject(new Error(m.message)); }
      };
      w.onerror = (e) => { e.preventDefault?.(); w.terminate(); this.worker = null; reject(new Error('worker failed')); };
      w.postMessage({ text: this.text, opts });
    });
  }

  // Fallback without workers: parse in slices and let the page breathe in between.
  async parseHere(opts) {
    const p = createParser(opts);
    const CH = 256 * 1024;
    for (let i = 0; i < this.text.length; i += CH) {
      p.push(this.text.slice(i, i + CH));
      this.setMsg(t('sim_parsing', { p: Math.round((i / this.text.length) * 100) }));
      await new Promise((r) => setTimeout(r, 0));
      if (this.closed) throw new Error('closed');
    }
    return p.end();
  }

  // ---------------------------------------------------------------- geometry
  // Projected points qx/qy for the current mode (top or tilted 3D).
  project() {
    const R = this.R, n1 = R.n + 1;
    this.qx = new Float32Array(n1); this.qy = new Float32Array(n1);
    const b = R.stats.bounds || { x0: 0, y0: 0, x1: 1, y1: 1 };
    const ext = Math.max(b.x1 - b.x0, b.y1 - b.y0, 1);
    const depth = Math.max(0.1, R.stats.zMax - R.stats.zMin);
    this.zk = Math.max(1, Math.min(30, (0.06 * ext) / depth));
    if (this.mode === 'top') {
      this.qx.set(R.px); this.qy.set(R.py);
    } else {
      const c = Math.cos(Math.PI / 6), s = Math.sin(Math.PI / 6), zk = this.zk;
      for (let i = 0; i < n1; i++) {
        const x = R.px[i], y = R.py[i];
        this.qx[i] = (x - y) * c;
        this.qy[i] = (x + y) * s * 0.9 + R.pz[i] * zk;
      }
    }
  }

  projPt(x, y, z = 0) {
    if (this.mode === 'top') return [x, y];
    const c = Math.cos(Math.PI / 6), s = Math.sin(Math.PI / 6);
    return [(x - y) * c, (x + y) * s * 0.9 + z * this.zk];
  }

  fit() {
    const r = this.stage.getBoundingClientRect();
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const add = (x, y) => { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; };
    const n1 = this.R.n + 1;
    const stepI = Math.max(1, Math.floor(n1 / 200000));
    for (let i = 0; i < n1; i += stepI) add(this.qx[i], this.qy[i]);
    const sh = this.R.sheet;
    if (sh) for (const [x, y] of [[sh.x0, sh.y0], [sh.x0 + sh.w, sh.y0], [sh.x0 + sh.w, sh.y0 + sh.h], [sh.x0, sh.y0 + sh.h]]) add(...this.projPt(x, y, 0));
    if (!Number.isFinite(x0)) { x0 = 0; y0 = 0; x1 = 1; y1 = 1; }
    const pad = 24;
    const w = Math.max(1, x1 - x0), hh = Math.max(1, y1 - y0);
    const s = Math.min((r.width - 2 * pad) / w, (r.height - 2 * pad) / hh);
    this.view = { s, ox: (r.width - w * s) / 2 - x0 * s, oy: (r.height + hh * s) / 2 + y0 * s };
    this.userMoved = false;
    this.fullRedraw();
  }

  toggleMode() {
    this.mode = this.mode === 'top' ? 'iso' : 'top';
    this.viewBtn.textContent = t(this.mode === 'top' ? 'sim_view_3d' : 'sim_view_top');
    this.project();
    this.renderLegend();
    this.fit();
  }

  // ---------------------------------------------------------------- canvases
  resize(refit) {
    const r = this.stage.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.dpr = dpr;
    for (const cv of [this.bg, this.prog, this.top]) {
      cv.width = Math.max(1, Math.round(r.width * dpr));
      cv.height = Math.max(1, Math.round(r.height * dpr));
      cv.style.width = `${r.width}px`; cv.style.height = `${r.height}px`;
    }
    this.W = r.width; this.H = r.height;
    if (refit) this.fit(); else this.fullRedraw();
  }

  colours() {
    const cs = getComputedStyle(this.el);
    const v = (k, d) => (cs.getPropertyValue(k) || '').trim() || d;
    return { sheet: v('--sim-sheet', '#ffffff'), edge: v('--border', '#d0d7de'), ghost: v('--sim-ghost', '#c3cad3'),
      text: v('--muted', '#59636e'), accent: v('--accent', '#1f6feb') };
  }

  ctxOf(cv) {
    const c = cv.getContext('2d');
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    return c;
  }

  // Redraw everything with the current view; bg + prog then match it again.
  fullRedraw() {
    if (!this.R) return;
    this.rv = { ...this.view };
    for (const cv of [this.bg, this.prog]) cv.style.transform = '';
    const col = this.colours();
    const g = this.ctxOf(this.bg);
    g.clearRect(0, 0, this.W, this.H);
    this.drawSheet(g, col);
    this.drawSegs(g, 0, this.R.n, this.rv, col.ghost);
    const p = this.ctxOf(this.prog);
    p.clearRect(0, 0, this.W, this.H);
    this.drawn = this.completed();
    this.drawSegs(p, 0, this.drawn, this.rv, null);
    this.drawTop();
  }

  // During pan / zoom: move the drawn canvases with CSS at once, redraw properly a moment later.
  viewChanged() {
    this.userMoved = true;
    const k = this.view.s / this.rv.s;
    const tx = this.view.ox - this.rv.ox * k, ty = this.view.oy - this.rv.oy * k;
    for (const cv of [this.bg, this.prog]) cv.style.transform = `translate(${tx}px, ${ty}px) scale(${k})`;
    this.drawTop();
    clearTimeout(this.fullTimer);
    this.fullTimer = setTimeout(() => this.fullRedraw(), this.R && this.R.n > 50000 ? 180 : 40);
  }

  drawSheet(g, col) {
    const sh = this.R.sheet;
    if (!sh) return;
    const v = this.rv;
    const pts = [[sh.x0, sh.y0], [sh.x0 + sh.w, sh.y0], [sh.x0 + sh.w, sh.y0 + sh.h], [sh.x0, sh.y0 + sh.h]]
      .map(([x, y]) => this.projPt(x, y, 0)).map(([x, y]) => [x * v.s + v.ox, -y * v.s + v.oy]);
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.closePath();
    g.fillStyle = col.sheet; g.fill();
    g.lineWidth = 1; g.strokeStyle = col.edge; g.setLineDash([]); g.stroke();
    // origin marker (X0 Y0)
    const [ox, oy] = this.projPt(0, 0, 0);
    const X = ox * v.s + v.ox, Y = -oy * v.s + v.oy;
    g.strokeStyle = col.accent; g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(X, Y); g.lineTo(X + 18, Y); g.moveTo(X, Y); g.lineTo(X, Y - 18); g.stroke();
    g.fillStyle = col.text; g.font = '11px system-ui, sans-serif';
    g.fillText('X0 Y0', X + 4, Y + 13);
  }

  // Draw segments [a, b) batched by colour. ghost = one colour for everything (rapids dashed).
  drawSegs(g, a, b, v, ghost) {
    if (b <= a) return;
    const R = this.R, qx = this.qx, qy = this.qy, kind = R.kind, bin = R.bin;
    const paths = new Map();
    const lastEnd = new Map();
    const s = v.s, ox = v.ox, oy = v.oy;
    for (let i = a; i < b; i++) {
      const key = kind[i] === 0 ? 'r' : (ghost ? 'c' : bin[i]);
      let p = paths.get(key);
      if (!p) { p = new Path2D(); paths.set(key, p); }
      if (lastEnd.get(key) !== i) p.moveTo(qx[i] * s + ox, -qy[i] * s + oy);
      p.lineTo(qx[i + 1] * s + ox, -qy[i + 1] * s + oy);
      lastEnd.set(key, i + 1);
    }
    g.lineJoin = 'round'; g.lineCap = 'round';
    for (const [key, p] of paths) {
      if (key === 'r') {
        g.setLineDash([5, 4]); g.lineWidth = 1; g.strokeStyle = ghost ? ghost : RAPID_COLOUR;
        g.globalAlpha = ghost ? 0.7 : 0.85;
      } else {
        g.setLineDash([]); g.lineWidth = ghost ? 1 : 1.6; g.globalAlpha = 1;
        g.strokeStyle = ghost || (key === 255 ? AIR_COLOUR : this.binColour(key));
      }
      g.stroke(p);
    }
    g.setLineDash([]); g.globalAlpha = 1;
  }

  binColour(k) {
    const L = this.R.levels;
    if (L.discrete) {
      const n = L.values.length;
      const idx = n <= 1 ? DEPTH_COLOURS.length - 1 : Math.round((k / (n - 1)) * (DEPTH_COLOURS.length - 1));
      return DEPTH_COLOURS[idx];
    }
    return DEPTH_COLOURS[k] || DEPTH_COLOURS[DEPTH_COLOURS.length - 1];
  }

  // Number of segments finished at the current time.
  completed() {
    const tc = this.R.tcum, T = this.simT;
    let lo = 0, hi = tc.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (tc[m] <= T + 1e-12) lo = m + 1; else hi = m; }
    return lo;
  }

  // Interpolated bit position and the running segment.
  current() {
    const R = this.R;
    const i = this.completed();
    if (i >= R.n) return { i: R.n, u: 1, x: R.px[R.n], y: R.py[R.n], z: R.pz[R.n], seg: R.n - 1 };
    const t0 = i ? R.tcum[i - 1] : 0, t1 = R.tcum[i];
    const u = t1 > t0 ? Math.max(0, Math.min(1, (this.simT - t0) / (t1 - t0))) : 0;
    const L = (a) => a[i] + (a[i + 1] - a[i]) * u;
    return { i, u, x: L(R.px), y: L(R.py), z: L(R.pz), seg: i };
  }

  drawTop() {
    if (!this.R) return;
    const g = this.ctxOf(this.top);
    g.clearRect(0, 0, this.W, this.H);
    const v = this.view;
    const c = this.current();
    const [px, py] = this.projPt(c.x, c.y, c.z);
    const X = px * v.s + v.ox, Y = -py * v.s + v.oy;
    if (c.i < this.R.n && c.u > 0) {
      const i = c.i;
      const sx = this.qx[i] * v.s + v.ox, sy = -this.qy[i] * v.s + v.oy;
      g.beginPath(); g.moveTo(sx, sy); g.lineTo(X, Y);
      const rapid = this.R.kind[i] === 0;
      g.setLineDash(rapid ? [5, 4] : []);
      g.lineWidth = rapid ? 1 : 2;
      g.strokeStyle = rapid ? RAPID_COLOUR : (this.R.bin[i] === 255 ? AIR_COLOUR : this.binColour(this.R.bin[i]));
      g.stroke(); g.setLineDash([]);
    }
    const d = this.R.header.toolD || 0;
    const rad = Math.max(5, (d / 2) * v.s);
    const above = c.z > 0.001;
    g.beginPath(); g.arc(X, Y, rad, 0, Math.PI * 2);
    g.fillStyle = above ? 'rgba(31,111,235,0.12)' : 'rgba(31,111,235,0.28)';
    g.fill();
    g.lineWidth = 2; g.strokeStyle = '#1f6feb'; g.stroke();
    g.beginPath(); g.moveTo(X - rad - 6, Y); g.lineTo(X + rad + 6, Y); g.moveTo(X, Y - rad - 6); g.lineTo(X, Y + rad + 6);
    g.lineWidth = 1; g.stroke();
  }

  // ---------------------------------------------------------------- playback
  togglePlay() {
    if (!this.R || !this.R.n) return;
    if (this.playing) { this.pause(); return; }
    if (this.simT >= this.total) this.setTime(0);
    this.playing = true;
    this.lastTs = 0;
    fill(this.playBtn, icon('pause', 18), h('span', {}, t('sim_pause')));
    this.playBtn.classList.add('on');
    this.raf = requestAnimationFrame((ts) => this.tick(ts));
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
    fill(this.playBtn, icon('play', 18), h('span', {}, t('sim_play')));
    this.playBtn.classList.remove('on');
  }

  tick(ts) {
    if (!this.playing || this.closed) return;
    const dt = this.lastTs ? Math.min(100, ts - this.lastTs) : 16;
    this.lastTs = ts;
    let T = this.simT + (dt / 60000) * this.speed;
    if (T >= this.total) { T = this.total; }
    this.advanceTo(T);
    if (T >= this.total) { this.pause(); return; }
    this.raf = requestAnimationFrame((x) => this.tick(x));
  }

  // Move the time; draws only the new segments when going forward.
  advanceTo(T) {
    this.simT = Math.max(0, Math.min(this.total, T));
    const done = this.completed();
    if (done > this.drawn) {
      const g = this.ctxOf(this.prog);
      this.drawSegs(g, this.drawn, done, this.rv, null);
      this.drawn = done;
    } else if (done < this.drawn) {
      const g = this.ctxOf(this.prog);
      g.clearRect(0, 0, this.W, this.H);
      this.drawSegs(g, 0, done, this.rv, null);
      this.drawn = done;
    }
    this.drawTop();
    this.updateReadout();
  }

  setTime(T) { this.advanceTo(T); }

  // Step to the end of the next move (dir 1) or back to the start of the last one (dir -1).
  // A move is one G-code line (an arc is many segments of one line).
  step(dir) {
    if (!this.R || !this.R.n) return;
    this.pause();
    const R = this.R, n = R.n;
    let c = this.completed();
    if (dir > 0) {
      if (c >= n) return;
      const L = R.line[c];
      let j = c;
      while (j < n && R.line[j] === L) j++;
      this.setTime(R.tcum[j - 1]);
    } else {
      if (c <= 0) { this.setTime(0); return; }
      const cur = this.current();
      if (cur.u > 0 && cur.i < n) c = cur.i + 1;      // inside a move: go to its start
      const L = R.line[c - 1];
      let j = c - 1;
      while (j > 0 && R.line[j - 1] === L) j--;
      this.setTime(j ? R.tcum[j - 1] : 0);
    }
  }

  // Jump to the end of the move on source line ln (warnings link here).
  gotoLine(ln) {
    const R = this.R;
    let lo = 0, hi = R.n;
    while (lo < hi) { const m = (lo + hi) >> 1; if (R.line[m] <= ln) lo = m + 1; else hi = m; }
    this.pause();
    this.setTime(lo ? R.tcum[lo - 1] : 0);
  }

  // ---------------------------------------------------------------- text
  updateReadout() {
    const R = this.R;
    if (!R) return;
    const c = this.current();
    const si = Math.min(c.i, R.n - 1);
    const rapid = R.n && R.kind[si] === 0;
    const f = R.n ? R.feed[si] : 0;
    const items = [['X', num(c.x, 3)], ['Y', num(c.y, 3)], ['Z', num(c.z, 3)], ['F', rapid ? t('sim_rapid_move') : num(f, 0)]];
    const ln = R.n ? R.line[si] : 0;
    fill(this.pos, items.map(([k, v]) => h('span', { class: 'sim-ax' }, h('span', { class: 'muted' }, k), h('strong', {}, v))),
      h('span', { class: 'sim-ax' }, h('span', { class: 'muted' }, t('sim_line')), h('strong', {}, ln ? num(ln) : '–')));
    this.code.textContent = ln ? this.lineText(ln) : '';
    this.slider.value = String(this.total ? Math.round((this.simT / this.total) * SLIDER_MAX) : 0);
    this.timeEl.textContent = `${clock(this.simT)} / ${clock(this.total)}`;
  }

  lineText(ln) {
    const ls = this.R.lineStart;
    if (ln < 1 || ln >= ls.length) return '';
    return this.text.slice(ls[ln - 1], ls[ln]).replace(/\r?\n$/, '').slice(0, 200);
  }

  renderStats() {
    const s = this.R.stats;
    const len = (mm) => (mm >= 10000 ? `${num(mm / 1000, 2)} m` : `${num(mm, 0)} mm`);
    const b = s.cutBounds;
    const rows = [
      [t('sim_lines'), num(s.lines)],
      [t('sim_moves'), num(s.moves)],
      [t('sim_cut'), len(s.cutMm)],
      [t('sim_rapid'), len(s.rapidMm)],
      [t('sim_time'), t('minutes_n', { n: num(s.minutes, 1) })],
      [t('sim_zmin'), `${num(s.zMin, 3)} mm`],
      b ? [t('sim_area'), `${num(b.x1 - b.x0, 1)} × ${num(b.y1 - b.y0, 1)} mm`] : null,
      Number.isFinite(this.R.expectZ) ? [t('sim_expect'), `${num(this.R.expectZ, 2)} mm`] : null,
    ].filter(Boolean);
    fill(this.stats, rows.map(([k, v]) => h('div', { class: 'stat' }, h('span', { class: 'stat-label' }, k), h('span', { class: 'stat-value' }, v))));
  }

  renderWarnings() {
    const ws = this.R.warnings;
    if (!ws.length) { fill(this.warns, h('p', { class: 'sim-ok' }, icon('check', 16), t('sim_no_warnings'))); return; }
    fill(this.warns, h('ul', {}, ws.map((w) => h('li', { class: 'sim-warn', 'data-code': w.code },
      h('span', {}, t(`simw_${w.code}`, { n: num(w.count), z: num(w.deepest ?? 0, 3), e: num(w.expect ?? 0, 2), word: w.word || '' })),
      w.lines && w.lines.length && w.code !== 'no_m30'
        ? h('span', { class: 'sim-lines' }, ' ', t('sim_line'), ' ', w.lines.map((ln, i) => [i ? ', ' : '',
          h('button', { class: 'linkish', onclick: () => this.gotoLine(ln) }, String(ln))]), w.count > w.lines.length ? ' …' : '')
        : null))));
  }

  renderLegend() {
    const L = this.R.levels;
    const items = [];
    if (L.discrete) {
      L.values.forEach((z, k) => items.push(h('span', { class: 'lg' }, h('i', { style: `background:${this.binColour(k)}` }), `Z ${num(z, 2)}`)));
    } else {
      items.push(h('span', { class: 'lg' }, `Z ${num(L.hi, 2)}`,
        h('i', { class: 'lg-bar', style: `background:linear-gradient(90deg, ${DEPTH_COLOURS.join(',')})` }), `${num(L.lo, 2)}`));
    }
    if (this.R.bin.includes(255)) items.push(h('span', { class: 'lg' }, h('i', { style: `background:${AIR_COLOUR}` }), t('sim_above')));
    items.push(h('span', { class: 'lg' }, h('i', { class: 'lg-dash' }), t('sim_rapid_move')));
    if (this.mode !== 'top') items.push(h('span', { class: 'lg muted' }, t('sim_zx', { k: num(this.zk, 0) })));
    fill(this.legend, items);
  }

  // ---------------------------------------------------------------- pan / zoom
  bindPointer() {
    const el = this.top;
    const pts = new Map();
    let last = null;
    const zoomAt = (k, cx, cy) => {
      const v = this.view;
      const s = Math.max(1e-4, Math.min(v.s * k, 2000));
      k = s / v.s;
      this.view = { s, ox: cx - (cx - v.ox) * k, oy: cy - (cy - v.oy) * k };
      this.viewChanged();
    };
    el.addEventListener('wheel', (e) => {
      if (!this.R) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    el.addEventListener('pointerdown', (e) => {
      if (!this.R) return;
      el.setPointerCapture(e.pointerId);
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      last = null;
    });
    el.addEventListener('pointermove', (e) => {
      if (!pts.has(e.pointerId)) return;
      const prev = pts.get(e.pointerId);
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 1) {
        this.view = { ...this.view, ox: this.view.ox + e.clientX - prev.x, oy: this.view.oy + e.clientY - prev.y };
        this.viewChanged();
      } else if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const r = el.getBoundingClientRect();
        const cx = (a.x + b.x) / 2 - r.left, cy = (a.y + b.y) / 2 - r.top;
        if (last) {
          this.view = { ...this.view, ox: this.view.ox + cx - last.cx, oy: this.view.oy + cy - last.cy };
          zoomAt(d / last.d, cx, cy);
        }
        last = { d, cx, cy };
      }
    });
    const up = (e) => { pts.delete(e.pointerId); last = null; };
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  }
}

function clock(min) {
  const s = Math.max(0, Math.round(min * 60));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${mm}:${String(ss).padStart(2, '0')}`;
}

// The Simulate button used next to .nc files (job export list and relief files).
export function simButton(opts) {
  return h('button', { class: 'tbtn sim-open', testid: 'sim-open', title: t('sim_open'),
    onclick: (e) => { e.preventDefault(); openSimulator(opts()); } }, icon('play', 16), h('span', {}, t('sim_open')));
}

export const isNc = (name) => /\.(nc|tap|gcode|ngc)$/i.test(String(name || ''));
