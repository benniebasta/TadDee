// Settings (#/settings): machines, tools, materials (one generic table editor each), then System: health and backup.
import { api } from '../api.js';
import { t } from '../i18n.js';
import { h, field, select, busy, toast, toastError, confirmDialog, num, money, dateText, icon } from '../ui.js';

// Field list per entity. `id` = testid suffix (e.g. tool-diameter).
const ENTITIES = [
  {
    key: 'machine', path: '/machines', title: 'machines',
    fields: [
      { name: 'name', id: 'name', label: 'name', type: 'text', def: '' },
      { name: 'kind', id: 'kind', label: 'kind', type: 'select', def: 'cnc', options: () => [{ value: 'cnc', label: t('kind_cnc') }, { value: 'laser', label: t('kind_laser') }] },
      { name: 'bed_w', id: 'bed-w', label: 'bed_w', type: 'number', def: 2500 },
      { name: 'bed_h', id: 'bed-h', label: 'bed_h', type: 'number', def: 1300 },
      { name: 'rate_per_hour', id: 'rate', label: 'rate_per_hour', type: 'number', def: 0, step: '1' },
      { name: 'notes', id: 'notes', label: 'notes', type: 'text', def: '' },
    ],
    cols: [
      ['name', (m) => m.name], ['kind', (m) => t(`kind_${m.kind}`, {}, m.kind)],
      ['bed_mm', (m) => `${num(m.bed_w)} × ${num(m.bed_h)}`, 'num'],
      ['rate_per_hour', (m) => `${money(m.rate_per_hour)}`, 'num'], ['notes', (m) => m.notes ?? ''],
    ],
  },
  {
    key: 'tool', path: '/tools', title: 'tools',
    fields: [
      { name: 'name', id: 'name', label: 'name', type: 'text', def: '' },
      { name: 'diameter_mm', id: 'diameter', label: 'diameter_mm', type: 'number', def: 6, step: '0.1' },
      { name: 'kind', id: 'kind', label: 'kind', type: 'select', def: 'flat', options: () => ['flat', 'ball', 'vbit'].map((k) => ({ value: k, label: t(`kind_${k}`) })) },
      { name: 'angle_deg', id: 'angle', label: 'angle_deg', type: 'number', def: '', step: '1', nullable: true },
      { name: 'pass_depth_mm', id: 'pass', label: 'pass_depth_mm', type: 'number', def: 3, step: '0.1' },
      { name: 'feed_mm_min', id: 'feed', label: 'feed_mm_min', type: 'number', def: 3000, step: '10' },
      { name: 'plunge_mm_min', id: 'plunge', label: 'plunge_mm_min', type: 'number', def: 800, step: '10' },
      { name: 'rpm', id: 'rpm', label: 'rpm', type: 'number', def: 18000, step: '100', int: true },
    ],
    cols: [
      ['name', (x) => x.name], ['diameter_mm', (x) => num(x.diameter_mm, 2), 'num'],
      ['kind', (x) => `${t(`kind_${x.kind}`, {}, x.kind)}${x.kind === 'vbit' && x.angle_deg ? ` ${num(x.angle_deg)}°` : ''}`],
      ['pass_depth_mm', (x) => num(x.pass_depth_mm, 2), 'num'], ['feed_mm_min', (x) => num(x.feed_mm_min), 'num'],
      ['plunge_mm_min', (x) => num(x.plunge_mm_min), 'num'], ['rpm', (x) => num(x.rpm), 'num'],
    ],
  },
  {
    key: 'material', path: '/materials', title: 'materials',
    fields: [
      { name: 'name', id: 'name', label: 'name', type: 'text', def: '' },
      { name: 'thickness_mm', id: 'thickness', label: 'thickness_mm', type: 'number', def: 3, step: '0.1' },
      { name: 'sheet_w', id: 'sheet-w', label: 'sheet_w', type: 'number', def: 2440 },
      { name: 'sheet_h', id: 'sheet-h', label: 'sheet_h', type: 'number', def: 1220 },
      { name: 'margin_mm', id: 'margin', label: 'margin_mm', type: 'number', def: 10, step: '0.5' },
      { name: 'price_per_sheet', id: 'price', label: 'price_per_sheet', type: 'number', def: 0, step: '0.01', hint: 'price_hint' },
    ],
    cols: [
      ['name', (x) => x.name], ['thickness_mm', (x) => num(x.thickness_mm, 2), 'num'],
      ['sheet_mm', (x) => `${num(x.sheet_w)} × ${num(x.sheet_h)}`, 'num'], ['margin_mm', (x) => num(x.margin_mm, 1), 'num'],
      ['price_per_sheet', (x) => (Number(x.price_per_sheet) ? money(x.price_per_sheet)
        : h('span', { class: 'badge warn' }, t('set_price_short'))), 'num'],
    ],
  },
];

export async function render(root, _params, isCurrent) {
  const sections = ENTITIES.map((e) => editor(e));
  if (!isCurrent()) return;
  const sys = systemPanel();
  root.replaceChildren(h('h1', {}, t('nav_settings')), ...sections.map((s) => s.el), sys.el);
  await Promise.all([...sections.map((s) => s.load()), sys.load()]);
}

// System: health of the server and the backup ("Back up now", last backup time).
function systemPanel() {
  const info = h('dl', { class: 'kv', testid: 'health-info' });
  const last = h('strong', { testid: 'backup-last' }, '…');
  const folder = h('span', { class: 'muted small', testid: 'backup-folder' });
  const btn = h('button', { class: 'btn primary', testid: 'backup-now' }, t('backup_now'));
  const row = (k, v, cls) => [h('dt', {}, k), h('dd', { class: cls || '' }, v)];
  async function load() {
    try {
      const [hl, b] = await Promise.all([api.get('/health'), api.get('/admin/backup')]);
      const up = Math.max(0, Math.round(Number(hl.uptime_s) || 0) / 60);
      info.replaceChildren(
        ...row(t('db_status'), hl.db_ok ? t('status_ok') : t('status_problem'), hl.db_ok ? 'ok-text' : 'danger-text'),
        ...row(t('version'), hl.version ?? ''),
        ...row(t('uptime'), t('uptime_hm', { h: Math.floor(up / 60), m: Math.floor(up % 60) })),
        ...row(t('disk_free'), hl.disk_free_mb != null ? `${num(hl.disk_free_mb / 1000, 1)} GB` : '?'),
        ...row(t('queued_tasks'), String(hl.worker?.queued ?? 0)));
      last.textContent = b.time ? dateText(b.time) : t('backup_never');
      folder.textContent = b.folder ? `${t('backup_folder')}: ${b.folder}` : '';
    } catch (e) { toastError(e); }
  }
  btn.addEventListener('click', () => busy(btn, async () => {
    const b = await api.post('/admin/backup', {});
    toast(t('backup_done', { path: b.path }), 'ok');
    await load();
  }, t('backing_up')));
  const el = h('section', { class: 'card', testid: 'system-panel' },
    h('h2', {}, icon('backup', 20), t('system')),
    h('h3', {}, t('health')), info,
    h('h3', {}, t('backup')),
    h('div', { class: 'row wrap' }, h('span', {}, `${t('backup_last')}: `), last, h('span', { class: 'grow' }), btn),
    folder ? h('p', {}, folder) : null,
    h('p', { class: 'muted small' }, t('backup_note')));
  return { el, load };
}

function editor(E) {
  const tbody = h('tbody');
  const formBox = h('div', { class: 'card inner hidden', testid: `${E.key}-form` });
  let items = [];

  async function load() {
    try { items = await api.get(E.path); } catch (e) { toastError(e); items = []; }
    tbody.replaceChildren(...items.map((it) => {
      const edit = h('button', { class: 'btn', testid: `${E.key}-edit`, onclick: () => openForm(it) }, t('edit'));
      const del = h('button', { class: 'btn danger-text', testid: `${E.key}-delete` }, t('delete'));
      del.addEventListener('click', async () => {
        if (!(await confirmDialog(t('confirm_delete', { name: it.name }), { ok: t('delete'), danger: true }))) return;
        await busy(del, async () => { await api.del(`${E.path}/${encodeURIComponent(it.id)}`); await load(); toast(t('deleted'), 'ok'); });
      });
      return h('tr', { testid: `${E.key}-row`, 'data-id': it.id },
        E.cols.map(([, fn, cls], i) => h('td', { class: i === 0 ? 'name-cell' : cls || '' }, fn(it))),
        h('td', { class: 'actions' }, edit, del));
    }));
    if (!items.length) tbody.replaceChildren(h('tr', {}, h('td', { colspan: String(E.cols.length + 1), class: 'empty' }, t('none_yet'))));
  }

  // Add (it = null) or edit form.
  function openForm(it) {
    const inputs = {};
    const ctrls = E.fields.map((f) => {
      const val = it ? it[f.name] ?? '' : f.def;
      let c;
      if (f.type === 'select') c = select({ testid: `${E.key}-${f.id}` }, f.options(), val);
      else c = h('input', { type: f.type, testid: `${E.key}-${f.id}`, value: String(val ?? ''), step: f.step || 'any',
        min: f.type === 'number' ? '0' : undefined, inputmode: f.type === 'number' ? 'decimal' : undefined, autocomplete: 'off' });
      inputs[f.name] = c;
      return field(t(f.label), c, f.hint ? t(f.hint) : undefined);
    });
    const save = h('button', { class: 'btn primary', type: 'submit', testid: `${E.key}-save` }, t('save'));
    const form = h('form', { class: 'grid-form', onsubmit: (ev) => {
      ev.preventDefault();
      busy(save, async () => {
        const body = {};
        for (const f of E.fields) {
          const raw = String(inputs[f.name].value).trim();
          if (f.type === 'number') {
            if (raw === '') { if (f.nullable) { body[f.name] = null; continue; } throw new Error(t('err_required', { f: t(f.label) })); }
            const n = Number(raw);
            if (!Number.isFinite(n)) throw new Error(t('err_number', { f: t(f.label) }));
            body[f.name] = f.int ? Math.round(n) : n;
          } else body[f.name] = raw;
        }
        if (!body.name) throw new Error(t('err_required', { f: t('name') }));
        if (E.key === 'tool' && body.kind !== 'vbit') body.angle_deg = null;
        if (it) await api.put(`${E.path}/${encodeURIComponent(it.id)}`, body);
        else await api.post(E.path, body);
        formBox.classList.add('hidden');
        await load();
        toast(t('saved'), 'ok');
      });
    } },
    ...ctrls,
    h('div', { class: 'row end span-all' },
      h('button', { class: 'btn', type: 'button', onclick: () => formBox.classList.add('hidden') }, t('cancel')), save));
    formBox.replaceChildren(h('h3', {}, it ? t('edit_x', { name: it.name }) : t(`add_${E.key}`)), form);
    formBox.classList.remove('hidden');
    inputs.name.focus();
  }

  const el = h('section', { class: 'card' },
    h('div', { class: 'row between' }, h('h2', {}, t(E.title)),
      h('button', { class: 'btn primary', testid: `${E.key}-add`, onclick: () => openForm(null) }, '+ ', t(`add_${E.key}`))),
    formBox,
    h('div', { class: 'table-wrap' }, h('table', { testid: `settings-${E.title}` },
      h('thead', {}, h('tr', {}, E.cols.map(([k, , cls]) => h('th', { class: cls || '' }, t(k))), h('th', {}, ''))),
      tbody)));
  return { el, load };
}
