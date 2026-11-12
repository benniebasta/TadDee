// Job page (#/jobs/:id): Parts -> Nest -> Export on one page.
import { api, url } from '../api.js';
import { t } from '../i18n.js';
import { h, fill, field, select, busy, toast, toastError, confirmDialog, svgBox,
  num, money, pct, idOf, numVal, icon, panelSection, canvasView } from '../ui.js';
import { idNum } from './jobs.js';
import { simButton, isNc } from './sim.js';

// Nest option tables (values are stable for tests; labels are translated).
const ROTATIONS = [
  { value: '0/180', angles: [0, 180], key: 'rot_0_180' },
  { value: '0/90/180/270', angles: [0, 90, 180, 270], key: 'rot_90' },
  { value: 'every45', angles: range(0, 360, 45), key: 'rot_45' },
  { value: 'every15', angles: range(0, 360, 15), key: 'rot_15' },
];
const QUALITY = [
  { value: 'fast', resolution_mm: 3, time_limit_s: 5, key: 'q_fast' },
  { value: 'normal', resolution_mm: 2, time_limit_s: 10, key: 'q_normal' },
  { value: 'fine', resolution_mm: 1, time_limit_s: 30, key: 'q_fine' },
];
function range(a, b, s) { const r = []; for (let x = a; x < b; x += s) r.push(x); return r; }
const countOf = (v) => (Array.isArray(v) ? v.length : Number(v) || 0);

export async function render(root, { id }, isCurrent) {
  const S = { job: null, materials: [], machines: [], tools: [], warnings: [], gapTouched: false, active: 0 };
  try {
    [S.job, S.materials, S.machines, S.tools] = await Promise.all([
      api.get(`/jobs/${encodeURIComponent(id)}`), api.get('/materials'), api.get('/machines'), api.get('/tools')]);
  } catch (e) {
    if (!isCurrent()) return;
    toastError(e);
    root.replaceChildren(h('p', { class: 'empty' }, e.message), h('a', { class: 'btn', href: '#/jobs' }, t('back_jobs')));
    return;
  }
  if (!isCurrent()) return;
  const jid = encodeURIComponent(S.job.id ?? id);

  // ---- lookups (job.material/machine may be objects or ids) ----
  const material = () => {
    const m = S.job.material, mid = idOf(m) ?? S.job.material_id;
    const full = S.materials.find((x) => String(x.id) === String(mid));
    return { ...(full || {}), ...(m && typeof m === 'object' ? m : {}) };
  };
  const machine = () => {
    const m = S.job.machine, mid = idOf(m) ?? S.job.machine_id;
    const full = S.machines.find((x) => String(x.id) === String(mid));
    return { ...(full || {}), ...(m && typeof m === 'object' ? m : {}) };
  };
  const isLaser = () => machine().kind === 'laser';
  const reloadJob = async () => { S.job = await api.get(`/jobs/${jid}`); return S.job; };

  // ---- header: name, status, material + machine (editable via PATCH) ----
  const statusBadge = h('span', { class: 'badge', testid: 'job-status' });
  const updateStatus = () => {
    statusBadge.className = `badge status-${S.job.status}`;
    statusBadge.textContent = t(`status_${S.job.status}`, {}, S.job.status ?? '');
  };
  const matSel = select({ testid: 'job-edit-material' },
    S.materials.map((m) => ({ value: m.id, label: m.name })), material().id);
  const macSel = select({ testid: 'job-edit-machine' },
    S.machines.map((m) => ({ value: m.id, label: `${m.name}` })), machine().id);
  const patchJob = async (body, sel, oldVal) => {
    sel.disabled = true;
    try {
      const j = await api.patch(`/jobs/${jid}`, body);
      S.job = j && j.id !== undefined ? j : await reloadJob();
      updateStatus();
      if (!S.gapTouched) gapIn.value = defaultGap();
      renderNestResult();
      renderExport();
      toast(t('saved'), 'ok');
    } catch (e) {
      toastError(e);
      sel.value = oldVal;
    } finally { sel.disabled = false; }
  };
  let matOld = matSel.value, macOld = macSel.value;
  matSel.addEventListener('change', () => { const o = matOld; matOld = matSel.value; patchJob({ material_id: idNum(matSel.value) }, matSel, o); });
  macSel.addEventListener('change', () => { const o = macOld; macOld = macSel.value; patchJob({ machine_id: idNum(macSel.value) }, macSel, o); });

  // ---- 1. Parts ----
  const warnBox = h('div', { class: 'warnings hidden', testid: 'import-warnings', role: 'status' });
  const partsBody = h('tbody');
  const partsEmpty = h('p', { class: 'empty' }, t('no_parts'));
  const fileInput = h('input', { type: 'file', testid: 'file-input', accept: '.svg,.dxf,.pdf,.ai', multiple: true, id: 'file-input' });
  const dropLabel = h('span', {}, t('drop_files'));
  const drop = h('label', { class: 'dropzone', for: 'file-input' }, icon('upload', 22), h('strong', {}, t('choose_files')), dropLabel,
    h('span', { class: 'muted small' }, t('file_types')));

  async function upload(files) {
    if (!files || !files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append('file', f, f.name);
    drop.classList.add('busy');
    dropLabel.textContent = t('uploading');
    try {
      const res = await api.upload(`/jobs/${jid}/files`, fd);
      S.warnings = res?.warnings ?? [];
      try { await reloadJob(); } catch { S.job.parts = [...(S.job.parts || []), ...(res?.parts || [])]; }
      renderParts();
      updateStatus();
      toast(t('imported_n', { n: (res?.parts || []).length }), 'ok');
    } catch (e) {
      toastError(e);
    } finally {
      drop.classList.remove('busy');
      dropLabel.textContent = t('drop_files');
      fileInput.value = '';
    }
  }
  fileInput.addEventListener('change', () => upload([...fileInput.files]));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); upload([...e.dataTransfer.files]); });

  // qty PATCH, debounced 300 ms per part. Pending saves are flushed before Nest runs.
  const qtyPending = new Map(); // part id -> {timer, run}
  const inflight = new Set();
  function qtyChanged(part, input) {
    const prev = qtyPending.get(part.id);
    if (prev) clearTimeout(prev.timer);
    const run = () => {
      qtyPending.delete(part.id);
      const pr = saveQty(part, input);
      inflight.add(pr);
      pr.finally(() => inflight.delete(pr));
      return pr;
    };
    qtyPending.set(part.id, { timer: setTimeout(run, 300), run });
  }
  async function saveQty(part, input) {
    const q = Math.round(Number(input.value));
    if (String(input.value).trim() === '' || !Number.isFinite(q) || q < 0 || q > 999) {
      toast(t('err_qty')); input.value = part.qty; return;
    }
    if (q === part.qty) return;
    try {
      const p = await api.patch(`/jobs/${jid}/parts/${encodeURIComponent(part.id)}`, { qty: q });
      part.qty = p?.qty ?? q;
      if (document.activeElement !== input || Number(input.value) === part.qty) input.value = part.qty;
      input.classList.add('saved');
      setTimeout(() => input.classList.remove('saved'), 800);
    } catch (e) { toastError(e); input.value = part.qty; }
  }
  async function flushQty() {
    for (const p of [...qtyPending.values()]) { clearTimeout(p.timer); p.run(); }
    await Promise.allSettled([...inflight]);
  }

  function renderParts() {
    const ws = S.warnings || [];
    warnBox.replaceChildren(...(ws.length ? [h('strong', {}, t('import_warnings')), h('ul', {}, ws.map((w) => h('li', {}, w)))] : []));
    warnBox.classList.toggle('hidden', !ws.length);
    const parts = S.job.parts || [];
    partsEmpty.classList.toggle('hidden', parts.length > 0);
    partsBody.replaceChildren(...parts.map((p) => {
      const qty = h('input', { type: 'number', min: '0', max: '999', step: '1', value: String(p.qty ?? 1),
        testid: 'part-qty', class: 'qty', inputmode: 'numeric', 'aria-label': t('qty') });
      qty.addEventListener('input', () => qtyChanged(p, qty));
      qty.addEventListener('change', () => qtyChanged(p, qty));
      const del = h('button', { class: 'btn icon danger-text', testid: 'part-delete', title: t('delete'), 'aria-label': t('delete') }, '✕');
      del.addEventListener('click', () => busy(del, async () => {
        await api.del(`/jobs/${jid}/parts/${encodeURIComponent(p.id)}`);
        S.job.parts = (S.job.parts || []).filter((x) => x.id !== p.id);
        renderParts();
      }, ' '));
      const marks = countOf(p.marks);
      return h('tr', { testid: 'part-row', 'data-id': p.id },
        h('td', { class: 'thumb-cell' }, svgBox(p.svg, 'thumb')),
        h('td', {}, h('div', {}, p.name), marks ? h('div', { class: 'muted small' }, t('marks_n', { n: marks, layer: p.mark_layer ?? '' })) : null),
        h('td', { class: 'num nowrap' }, `${num(p.width, 1)} × ${num(p.height, 1)}`),
        h('td', { class: 'num hide-sm' }, countOf(p.holes)),
        h('td', {}, qty),
        h('td', { class: 'num' }, del));
    }));
    renderCanvas();
    renderStatus();
  }

  const partsCard = panelSection({ title: t('parts'), step: '1', testid: 'panel-parts' },
    drop, fileInput, warnBox,
    h('div', { class: 'table-wrap' }, h('table', { testid: 'parts-table', class: 'compact' },
      h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, t('name')), h('th', { class: 'num' }, t('size_mm')),
        h('th', { class: 'num hide-sm' }, t('holes')), h('th', {}, t('qty')), h('th', {}, ''))),
      partsBody)),
    partsEmpty);

  // ---- 2. Nest ----
  const st = S.job.settings || {};
  const cutTool = () => S.tools.find((x) => String(x.id) === String(toolSel?.value)) || defaultCutTool();
  const defaultCutTool = () => S.tools.find((x) => String(x.id) === String(st.cut_tool_id))
    || S.tools.find((x) => x.kind === 'flat' && Number(x.diameter_mm) === 6)
    || S.tools.find((x) => x.kind === 'flat') || S.tools[0];
  const defaultGap = () => (isLaser() ? 2 : Number(cutTool()?.diameter_mm ?? 5) + 1);

  const gapIn = h('input', { type: 'number', min: '0', step: '0.5', testid: 'nest-gap', inputmode: 'decimal' });
  gapIn.addEventListener('input', () => { S.gapTouched = true; });
  const rotMatch = S.job.nest && Array.isArray(st.rotations)
    ? ROTATIONS.find((r) => r.angles.length === st.rotations.length && r.angles.every((a, i) => Number(st.rotations[i]) === a)) : null;
  const rotSel = select({ testid: 'nest-rotations' }, ROTATIONS.map((r) => ({ value: r.value, label: t(r.key) })), rotMatch?.value ?? '0/90/180/270');
  const qMatch = S.job.nest && QUALITY.find((q) => q.resolution_mm === Number(st.resolution_mm));
  const qualSel = select({ testid: 'nest-quality' }, QUALITY.map((q) => ({ value: q.value, label: t(q.key) })), qMatch?.value ?? 'normal');
  const holesChk = h('input', { type: 'checkbox', testid: 'nest-holes', checked: st.allow_in_holes ?? true });
  const offcutChk = h('input', { type: 'checkbox', testid: 'nest-offcuts', checked: st.use_offcuts ?? false });
  const sheetW = h('input', { type: 'number', min: '1', step: '1', testid: 'nest-sheet-w', inputmode: 'decimal' });
  const sheetH = h('input', { type: 'number', min: '1', step: '1', testid: 'nest-sheet-h', inputmode: 'decimal' });
  const nestBtn = h('button', { class: 'btn primary big block', testid: 'nest-run' }, icon('nest', 18), t('nest'));
  const nestResult = h('div', { class: 'nest-result' });

  const setSheetPlaceholders = () => {
    const m = material();
    sheetW.placeholder = m.sheet_w ? String(m.sheet_w) : '';
    sheetH.placeholder = m.sheet_h ? String(m.sheet_h) : '';
  };
  // settings holds defaults before the first nest; only reuse them once the job was nested
  if (st.gap_mm != null && S.job.nest) { gapIn.value = st.gap_mm; S.gapTouched = true; }

  nestBtn.addEventListener('click', () => busy(nestBtn, async () => {
    await flushQty();
    const q = QUALITY.find((x) => x.value === qualSel.value) || QUALITY[1];
    const r = ROTATIONS.find((x) => x.value === rotSel.value) || ROTATIONS[1];
    const body = { gap_mm: numVal(gapIn) ?? defaultGap(), rotations: r.angles, allow_in_holes: holesChk.checked,
      resolution_mm: q.resolution_mm, time_limit_s: q.time_limit_s, use_offcuts: offcutChk.checked };
    const w = numVal(sheetW), hh = numVal(sheetH);
    if (w) body.sheet_w = w;
    if (hh) body.sheet_h = hh;
    const res = await api.post(`/jobs/${jid}/nest`, body);
    try { await reloadJob(); } catch { /* keep old job */ }
    if (res && Array.isArray(res.sheets)) S.job.nest = res;
    if (!S.job.exports) S.job.exports = [];
    updateStatus();
    renderNestResult();
    renderExport();
  }, t('nesting')));

  function costLine(nest) {
    const c = nest.cost || {};
    const price = Number(material().price_per_sheet);
    const mins = c.cut_minutes != null ? t('cut_time', { m: num(c.cut_minutes, 0) }) : '';
    if (!price) {
      return h('span', {}, h('a', { href: '#/settings', testid: 'cost-set-price' }, t('set_price')),
        mins ? h('span', { class: 'muted' }, ` · ${mins}`) : null);
    }
    return h('span', {}, h('strong', {}, `${money(c.total_thb)} THB`), ` ${t('excl_vat')}`,
      h('span', { class: 'muted' }, ` (${t('sheets_cost')} ${money(c.sheets_thb)} + ${t('machine_cost')} ${money(c.machine_thb)})`),
      mins ? h('span', { class: 'muted' }, ` · ${mins}`) : null);
  }

  function renderNestResult() {
    setSheetPlaceholders();
    const nest = S.job.nest;
    if (!nest || !Array.isArray(nest.sheets)) {
      nestResult.replaceChildren(h('p', { class: 'muted', testid: 'nest-empty' }, t('not_nested')));
      renderStrip();
      renderCanvas();
      renderStatus();
      return;
    }
    const unplaced = nest.unplaced || [];
    const unplacedCount = unplaced.reduce((s, u) => s + (Number(u.count) || 0), 0);
    const summary = h('div', { class: 'summary', testid: 'nest-summary' },
      stat(t('sheets_used'), nest.sheets_used ?? nest.sheets.length, 'sum-sheets'),
      stat(t('utilisation'), pct(nest.utilisation), 'sum-util'),
      stat(t('unplaced'), unplacedCount, 'sum-unplaced', unplacedCount ? 'warn' : ''),
      h('div', { class: 'stat wide', testid: 'sum-cost' }, h('span', { class: 'stat-label' }, t('cost')), costLine(nest)));
    fill(nestResult, summary,
      unplaced.length ? h('div', { class: 'warnings', testid: 'nest-unplaced' }, h('strong', {}, t('unplaced_parts')),
        h('ul', {}, unplaced.map((u) => h('li', {}, `${u.name ?? u.part_id} × ${u.count}`)))) : null,
      nest.attempts != null ? h('p', { class: 'muted small' }, t('nest_meta', { a: nest.attempts, s: num(nest.elapsed_s, 1) })) : null);
    if (S.active >= nest.sheets.length) S.active = 0;
    renderStrip();
    renderCanvas();
    renderStatus();
  }

  // ---- sheet strip under the canvas: one thumbnail per sheet, click to show it ----
  const strip = h('div', { class: 'strip', testid: 'sheet-strip' });
  function renderStrip() {
    const nest = S.job.nest;
    if (!nest || !Array.isArray(nest.sheets) || !nest.sheets.length) {
      strip.classList.add('hidden');
      strip.replaceChildren();
      return;
    }
    strip.classList.remove('hidden');
    strip.replaceChildren(...nest.sheets.map((s, i) => {
      const n = (s.index ?? i) + 1;
      const pick = () => { S.active = i; renderStrip(); renderCanvas(); };
      return h('figure', { class: `sheet${i === S.active ? ' active' : ''}`, testid: 'sheet-preview', 'data-index': s.index ?? i,
        tabindex: '0', title: t('click_show'), onclick: pick, onkeydown: (e) => { if (e.key === 'Enter') pick(); } },
      svgBox(s.svg),
      h('figcaption', { class: 'sheet-cap' }, h('strong', {}, `#${n}`), s.is_offcut ? h('span', { class: 'badge status-nested' }, t('offcut')) : null,
        h('span', { testid: 'sheet-util' }, pct(s.utilisation))));
    }));
  }

  function stat(label, value, testid, cls = '') {
    return h('div', { class: `stat ${cls}`, testid }, h('span', { class: 'stat-label' }, label), h('span', { class: 'stat-value' }, value));
  }

  const nestCard = panelSection({ title: t('nest'), step: '2', testid: 'panel-nest' },
    h('div', { class: 'grid-form two' },
      field(t('gap_mm'), gapIn, t('gap_hint')),
      field(t('rotations'), rotSel),
      field(t('quality'), qualSel),
      h('span'),
      field(t('sheet_w'), sheetW, t('sheet_hint')),
      field(t('sheet_h'), sheetH)),
    h('div', { class: 'checks' },
      h('label', { class: 'check' }, holesChk, t('parts_in_holes')),
      h('label', { class: 'check' }, offcutChk, t('use_offcuts'))),
    h('div', { class: 'row' }, nestBtn),
    nestResult);

  // ---- 3. Export ----
  let toolSel = null;
  const exportBody = h('div');
  const filesBox = h('div', { class: 'files', testid: 'export-files' });
  const zipLink = h('a', { class: 'btn', testid: 'export-zip', href: url(`/jobs/${jid}/export.zip`), download: '' }, t('download_zip'));
  const commitBtn = h('button', { class: 'btn primary', testid: 'job-commit' }, t('mark_cut'));
  commitBtn.addEventListener('click', async () => {
    const ok = await confirmDialog(t('confirm_cut'), { ok: t('mark_cut') });
    if (!ok) return;
    await busy(commitBtn, async () => {
      await api.post(`/jobs/${jid}/commit`, {});
      await reloadJob();
      updateStatus();
      renderExport();
      toast(t('marked_cut'), 'ok');
    });
  });

  function renderFiles() {
    const files = S.job.exports || [];
    const fileHref = (f) => f.url || url(`/jobs/${jid}/files/${encodeURIComponent(f.name)}`);
    filesBox.replaceChildren(...(files.length ? files.map((f) => h('div', { class: 'file' },
      h('a', { href: fileHref(f), download: f.name, testid: 'export-file' }, f.name),
      isNc(f.name) ? simButton(() => ({ url: fileHref(f), name: f.name, expectZ: expectedZ() })) : null,
      h('span', { class: 'muted small' }, [f.tool ? f.tool : null, f.minutes != null ? t('minutes_n', { n: num(f.minutes, 1) }) : null,
        f.size != null ? `${num(f.size / 1024, 1)} KB` : null].filter(Boolean).join(' · '))))
      : [h('p', { class: 'muted' }, t('no_files'))]));
    zipLink.classList.toggle('hidden', !files.length);
    commitBtn.disabled = !S.job.nest || S.job.status === 'cut';
    commitBtn.textContent = S.job.status === 'cut' ? t('already_cut') : t('mark_cut');
  }

  // Deepest Z the CNC file should reach: material thickness + cut-through (simulator check).
  let cutThrough = 0.3;
  function expectedZ() {
    const th = Number(material()?.thickness_mm);
    return Number.isFinite(th) && th > 0 ? -(th + cutThrough) : undefined;
  }

  function renderExport() {
    const prevTool = toolSel?.value;
    const toolOpts = S.tools.map((x) => ({ value: x.id, label: `${x.name} (${num(x.diameter_mm, 2)} mm)` }));
    let ctrls, btn;
    if (isLaser()) {
      const kerf = h('input', { type: 'number', min: '0', step: '0.05', value: '0', testid: 'export-kerf', inputmode: 'decimal' });
      const outline = h('input', { type: 'checkbox', testid: 'export-outline' });
      btn = h('button', { class: 'btn primary big block', testid: 'export-ruida' }, icon('export', 18), t('make_dxf'));
      btn.addEventListener('click', () => doExport(btn, { target: 'ruida', kerf_mm: numVal(kerf) ?? 0, include_sheet_outline: outline.checked }));
      ctrls = [field(t('kerf_mm'), kerf), h('div', { class: 'checks' }, h('label', { class: 'check' }, outline, t('sheet_outline')))];
      toolSel = null;
    } else {
      toolSel = select({ testid: 'export-tool' }, toolOpts, prevTool ?? defaultCutTool()?.id);
      toolSel.addEventListener('change', () => { if (!S.gapTouched) gapIn.value = defaultGap(); });
      const markSel = select({ testid: 'export-mark-tool' }, [{ value: '', label: t('same_as_cut') }, ...toolOpts], '');
      const through = h('input', { type: 'number', min: '0', step: '0.1', value: '0.3', testid: 'export-cut-through', inputmode: 'decimal' });
      const tabs = h('input', { type: 'checkbox', testid: 'export-tabs', checked: true });
      const tabW = h('input', { type: 'number', min: '0', step: '0.5', value: '6', testid: 'export-tab-width', inputmode: 'decimal' });
      const tabH = h('input', { type: 'number', min: '0', step: '0.1', value: '1.5', testid: 'export-tab-height', inputmode: 'decimal' });
      const climb = h('input', { type: 'checkbox', testid: 'export-climb', checked: true });
      btn = h('button', { class: 'btn primary big block', testid: 'export-ncstudio' }, icon('export', 18), t('make_gcode'));
      through.addEventListener('change', () => { if (!(S.job.exports || []).length) cutThrough = numVal(through) ?? 0.3; });
      btn.addEventListener('click', () => {
        cutThrough = numVal(through) ?? 0.3;
        const body = { target: 'ncstudio', cut_tool_id: idNum(toolSel.value),
          cam: { cut_through_mm: numVal(through) ?? 0.3, tabs: tabs.checked, tab_width_mm: numVal(tabW) ?? 6,
            tab_height_mm: numVal(tabH) ?? 1.5, climb: climb.checked } };
        if (markSel.value) body.mark_tool_id = idNum(markSel.value);
        doExport(btn, body);
      });
      ctrls = [field(t('cut_tool'), toolSel), field(t('mark_tool'), markSel), field(t('cut_through'), through),
        field(t('tab_width'), tabW), field(t('tab_height'), tabH),
        h('div', { class: 'checks' }, h('label', { class: 'check' }, tabs, t('tabs')), h('label', { class: 'check' }, climb, t('climb')))];
    }
    exportBody.replaceChildren(
      h('p', { class: 'muted', testid: 'export-kind' }, isLaser() ? t('export_laser', { m: machine().name ?? '' }) : t('export_cnc', { m: machine().name ?? '' })),
      h('div', { class: 'grid-form two' }, ctrls),
      h('div', { class: 'row' }, btn));
    renderFiles();
  }

  async function doExport(btn, body) {
    await busy(btn, async () => {
      const res = await api.post(`/jobs/${jid}/export`, body);
      try { await reloadJob(); } catch { /* ignore */ }
      if (!(S.job.exports || []).length && res?.files) S.job.exports = res.files;
      updateStatus();
      renderFiles();
      const ws = res?.warnings || [];
      if (ws.length) toast(ws.join(' '), 'warn'); else toast(t('files_ready', { n: (res?.files || []).length }), 'ok');
    }, t('making'));
  }

  const exportCard = panelSection({ title: t('export'), step: '3', testid: 'panel-export' },
    exportBody,
    h('h3', {}, t('files')), filesBox,
    h('div', { class: 'row wrap' }, zipLink, h('span', { class: 'grow' }), commitBtn));

  // ---- canvas: the active sheet, or all parts laid out before nesting ----
  const cv = canvasView({ testid: 'job-canvas' });
  const canvasTitle = h('span', { class: 'canvas-title', testid: 'canvas-title' });
  function renderCanvas() {
    const nest = S.job.nest;
    const parts = S.job.parts || [];
    if (nest && Array.isArray(nest.sheets) && nest.sheets.length) {
      const s = nest.sheets[S.active] || nest.sheets[0];
      canvasTitle.textContent = `${t('sheet')} ${(s.index ?? S.active) + 1} / ${nest.sheets.length} · ${num(s.width)} × ${num(s.height)} mm · ${pct(s.utilisation)}`
        + (s.offcut ? ` · ${t('leftover')} ${num(s.offcut.width)} × ${num(s.offcut.height)} mm` : '');
      cv.set(svgBox(s.svg, 'svg-fit sheet-doc'));
    } else if (parts.length) {
      canvasTitle.textContent = t('parts_board', { n: parts.reduce((a, p) => a + (Number(p.qty) || 0), 0) });
      cv.set(h('div', { class: 'parts-board' }, parts.map((p) => h('div', { class: 'part-tile' },
        svgBox(p.svg, 'tile-svg'),
        h('div', { class: 'tile-cap' }, h('span', { class: 'tile-name' }, p.name), h('span', { class: 'qty-pill' }, `× ${p.qty ?? 1}`)),
        h('div', { class: 'muted small' }, `${num(p.width, 1)} × ${num(p.height, 1)} mm`)))));
    } else {
      canvasTitle.textContent = t('canvas_empty_title');
      cv.set(h('label', { class: 'canvas-empty', for: 'file-input' }, icon('upload', 40),
        h('strong', {}, t('canvas_empty')), h('span', { class: 'muted' }, t('file_types'))));
    }
  }
  cv.el.addEventListener('dragover', (e) => { e.preventDefault(); cv.el.classList.add('over'); });
  cv.el.addEventListener('dragleave', () => cv.el.classList.remove('over'));
  cv.el.addEventListener('drop', (e) => { e.preventDefault(); cv.el.classList.remove('over'); upload([...e.dataTransfer.files]); });

  // ---- status bar: the numbers that matter at a glance ----
  const statusBar = h('footer', { class: 'statusbar', testid: 'status-bar' });
  function renderStatus() {
    const nest = S.job.nest;
    const parts = S.job.parts || [];
    const copies = parts.reduce((a, p) => a + (Number(p.qty) || 0), 0);
    const items = [
      [t('parts'), `${parts.length} · ${t('copies_n', { n: copies })}`],
      [t('machine'), `${machine().name ?? ''}`],
      [t('material'), material().name ?? ''],
    ];
    if (nest && Array.isArray(nest.sheets)) {
      const un = (nest.unplaced || []).reduce((a, u) => a + (Number(u.count) || 0), 0);
      items.push([t('sheets_used'), nest.sheets_used ?? nest.sheets.length], [t('utilisation'), pct(nest.utilisation)]);
      if (un) items.push([t('unplaced'), un, 'warn']);
      const c = nest.cost || {};
      if (Number(material().price_per_sheet)) items.push([t('cost'), `${money(c.total_thb)} THB ${t('excl_vat')}`]);
      if (c.cut_minutes != null) items.push([t('cut'), t('minutes_n', { n: num(c.cut_minutes, 0) })]);
    }
    statusBar.replaceChildren(...items.map(([k, v, cls]) => h('span', { class: `sb-item ${cls || ''}` }, h('span', { class: 'sb-k' }, k), h('strong', {}, v))));
  }

  // ---- delete job ----
  const delJob = h('button', { class: 'tbtn danger-text', testid: 'job-delete', title: t('delete_job'), 'aria-label': t('delete_job') }, icon('trash', 18), h('span', { class: 'hide-sm' }, t('delete_job')));
  delJob.addEventListener('click', async () => {
    if (!(await confirmDialog(t('confirm_delete_job', { name: S.job.name }), { ok: t('delete'), danger: true }))) return;
    await busy(delJob, async () => { await api.del(`/jobs/${jid}`); location.hash = '#/jobs'; });
  });

  const toolbar = h('header', { class: 'ws-toolbar' },
    h('a', { href: '#/jobs', class: 'tbtn', title: t('back_jobs'), 'aria-label': t('back_jobs') }, icon('back', 18)),
    h('h1', { testid: 'job-title' }, h('span', { class: 'title-text' }, S.job.name ?? ''), statusBadge),
    h('div', { class: 'tb-fields' }, h('label', { class: 'tb-field' }, h('span', {}, t('material')), matSel),
      h('label', { class: 'tb-field' }, h('span', {}, t('machine')), macSel)),
    h('span', { class: 'grow' }),
    delJob);

  fill(root, h('div', { class: 'ws' },
    toolbar,
    h('section', { class: 'ws-center' },
      h('div', { class: 'canvas-bar' }, canvasTitle, h('span', { class: 'grow' }), cv.controls),
      cv.el,
      strip),
    h('aside', { class: 'panel', testid: 'panel' }, partsCard, nestCard, exportCard),
    statusBar));

  updateStatus();
  renderExport(); // creates toolSel first so the gap default can use it
  if (!S.gapTouched) gapIn.value = defaultGap();
  renderParts();
  renderNestResult();

  return () => { flushQty(); }; // save pending qty edits when leaving the page
}
