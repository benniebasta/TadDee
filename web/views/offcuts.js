// Offcuts library (#/offcuts): leftovers saved by "Mark as cut", grouped by material.
// Two ways to add one by hand: type its size (phone), or upload a drawing whose
// largest closed shape is the remnant outline (holes in it = missing material).
import { api } from '../api.js';
import { t } from '../i18n.js';
import { h, fill, field, select, busy, toast, toastError, confirmDialog, num, numVal, dateText } from '../ui.js';

export async function render(root, _params, isCurrent) {
  let materials = [];
  try { materials = await api.get('/materials'); } catch (e) { toastError(e); }
  if (!isCurrent()) return;

  const filter = select({ testid: 'offcuts-material', 'aria-label': t('material') },
    [{ value: '', label: t('all_materials') }, ...materials.map((m) => ({ value: m.id, label: m.name }))], '');
  const showUsed = h('input', { type: 'checkbox', testid: 'offcuts-show-used' });
  const body = h('tbody');
  const empty = h('p', { class: 'empty hidden' }, t('no_offcuts'));
  const matName = (id) => materials.find((m) => String(m.id) === String(id))?.name ?? `#${id}`;

  async function load() {
    let list = [];
    try {
      list = await api.get(`/offcuts${filter.value ? `?material_id=${encodeURIComponent(filter.value)}` : ''}`);
    } catch (e) { toastError(e); }
    if (!showUsed.checked) list = list.filter((o) => !o.used);
    // group by material name, then biggest first
    list.sort((a, b) => matName(a.material_id).localeCompare(matName(b.material_id)) || b.width * b.height - a.width * a.height);
    const rows = [];
    let last = null;
    for (const o of list) {
      if (o.material_id !== last) {
        rows.push(h('tr', { class: 'group' }, h('th', { colspan: '6' }, matName(o.material_id))));
        last = o.material_id;
      }
      const del = h('button', { class: 'btn danger-text', testid: 'offcut-delete' }, t('delete'));
      del.addEventListener('click', async () => {
        if (!(await confirmDialog(t('confirm_delete_offcut'), { ok: t('delete'), danger: true }))) return;
        await busy(del, async () => { await api.del(`/offcuts/${encodeURIComponent(o.id)}`); await load(); });
      });
      rows.push(h('tr', { testid: 'offcut-row', 'data-id': o.id },
        h('td', {}, matName(o.material_id)),
        h('td', { class: 'num nowrap' }, `${num(o.width)} × ${num(o.height)}`,
          o.irregular ? h('span', { class: 'badge', testid: 'offcut-drawn', title: o.holes ? t('offcut_holes_n', { n: o.holes }) : '' }, ` ${t('offcut_drawn')}`) : null,
          o.name ? h('div', { class: 'muted small' }, o.name) : null),
        h('td', {}, o.source_job_id != null ? h('a', { href: `#/jobs/${encodeURIComponent(o.source_job_id)}` }, t('job_n', { n: o.source_job_id })) : ''),
        h('td', { class: 'muted' }, dateText(o.created_at)),
        h('td', {}, o.used ? h('span', { class: 'badge status-cut' }, t('used')) : h('span', { class: 'badge status-nested' }, t('free'))),
        h('td', { class: 'num' }, del)));
    }
    body.replaceChildren(...rows);
    empty.classList.toggle('hidden', list.length > 0);
  }
  filter.addEventListener('change', load);
  showUsed.addEventListener('change', load);

  // ---- add a remnant: by size, or from a drawing ----
  const matOpts = materials.map((m) => ({ value: m.id, label: m.name }));
  const addMat = select({ testid: 'offcut-add-material', 'aria-label': t('material') }, matOpts, materials[0]?.id ?? '');
  filter.addEventListener('change', () => { if (filter.value) addMat.value = filter.value; });
  const wIn = h('input', { type: 'number', min: '1', step: '1', inputmode: 'decimal', testid: 'offcut-w' });
  const hIn = h('input', { type: 'number', min: '1', step: '1', inputmode: 'decimal', testid: 'offcut-h' });
  const addBtn = h('button', { class: 'btn primary', type: 'button', testid: 'offcut-add' }, t('offcut_add_size'));
  addBtn.addEventListener('click', () => busy(addBtn, async () => {
    const w = numVal(wIn), hh = numVal(hIn);
    if (!addMat.value) throw new Error(t('err_required', { f: t('material') }));
    if (!w || !hh) throw new Error(t('offcut_err_size'));
    await api.post('/offcuts', { material_id: Number(addMat.value), width: w, height: hh });
    wIn.value = ''; hIn.value = '';
    toast(t('offcut_added'), 'ok');
    await load();
  }));
  const fileIn = h('input', { type: 'file', accept: '.svg,.dxf', testid: 'offcut-file', 'aria-label': t('offcut_drawing') });
  const upBtn = h('button', { class: 'btn', type: 'button', testid: 'offcut-upload' }, t('offcut_upload'));
  upBtn.addEventListener('click', () => busy(upBtn, async () => {
    const f = fileIn.files && fileIn.files[0];
    if (!addMat.value) throw new Error(t('err_required', { f: t('material') }));
    if (!f) throw new Error(t('offcut_err_file'));
    const fd = new FormData();
    fd.append('material_id', String(addMat.value));
    fd.append('file', f, f.name);
    const res = await api.upload('/offcuts/upload', fd);
    fileIn.value = '';
    toast((res?.warnings || []).length ? `${t('offcut_added')} · ${res.warnings.join(' · ')}` : t('offcut_added'), 'ok');
    await load();
  }));
  const addCard = h('section', { class: 'card inner', testid: 'offcut-add-form' },
    h('h3', {}, t('offcut_add_title')),
    h('div', { class: 'grid-form two' },
      field(t('material'), addMat),
      h('span'),
      field(t('width_mm'), wIn),
      field(t('height_mm_plain'), hIn)),
    h('div', { class: 'row wrap' }, addBtn),
    h('div', { class: 'row wrap' }, field(t('offcut_drawing'), fileIn, t('offcut_drawing_hint')), upBtn));

  fill(root, 
    h('h1', {}, t('nav_offcuts')),
    h('p', { class: 'muted' }, t('offcuts_intro')),
    addCard,
    h('div', { class: 'row wrap' }, filter, h('label', { class: 'check' }, showUsed, t('show_used'))),
    h('div', { class: 'table-wrap' }, h('table', { testid: 'offcuts-table' },
      h('thead', {}, h('tr', {}, h('th', {}, t('material')), h('th', { class: 'num' }, t('size_mm')), h('th', {}, t('from_job')),
        h('th', {}, t('created')), h('th', {}, t('status')), h('th', {}, ''))),
      body)),
    empty);
  await load();
}
