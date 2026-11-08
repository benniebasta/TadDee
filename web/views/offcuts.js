// Offcuts library (#/offcuts): leftovers saved by "Mark as cut", grouped by material.
import { api } from '../api.js';
import { t } from '../i18n.js';
import { h, fill, select, busy, toastError, confirmDialog, num, dateText } from '../ui.js';

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
        h('td', { class: 'num nowrap' }, `${num(o.width)} × ${num(o.height)}`),
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

  fill(root, 
    h('h1', {}, t('nav_offcuts')),
    h('p', { class: 'muted' }, t('offcuts_intro')),
    h('div', { class: 'row wrap' }, filter, h('label', { class: 'check' }, showUsed, t('show_used'))),
    h('div', { class: 'table-wrap' }, h('table', { testid: 'offcuts-table' },
      h('thead', {}, h('tr', {}, h('th', {}, t('material')), h('th', { class: 'num' }, t('size_mm')), h('th', {}, t('from_job')),
        h('th', {}, t('created')), h('th', {}, t('status')), h('th', {}, ''))),
      body)),
    empty);
  await load();
}
