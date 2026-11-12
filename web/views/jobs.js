// Jobs list (#/jobs) with a New job form, and the Bin (deleted jobs, 30 days).
import { api } from '../api.js';
import { t } from '../i18n.js';
import { h, fill, field, select, busy, toast, toastError, confirmDialog, pct, nameOf, dateText } from '../ui.js';

let showBin = false;   // Jobs | Bin filter, kept while the app is open

export async function render(root, _params, isCurrent) {
  let jobs = [], materials = [], machines = [];
  try {
    [jobs, materials, machines] = await Promise.all([api.get(showBin ? '/jobs?bin=1' : '/jobs'), api.get('/materials'),
      api.get('/machines')]);
  } catch (e) {
    toastError(e);
  }
  if (!isCurrent()) return;
  const again = () => render(root, _params, isCurrent);

  const formBox = h('div', { class: 'card hidden', testid: 'new-job-form' });
  const showForm = () => {
    formBox.classList.remove('hidden');
    const name = h('input', { type: 'text', testid: 'job-name', required: true, placeholder: t('job_name_ph'), autocomplete: 'off' });
    const mat = select({ testid: 'job-material' }, materials.map((m) => ({ value: m.id, label: `${m.name}` })));
    const mac = select({ testid: 'job-machine' }, machines.map((m) => ({ value: m.id, label: `${m.name}` })));
    const create = h('button', { class: 'btn primary', type: 'submit', testid: 'job-create' }, t('create_job'));
    const form = h('form', { class: 'grid-form', onsubmit: (e) => {
      e.preventDefault();
      busy(create, async () => {
        if (!name.value.trim()) throw new Error(t('err_job_name'));
        const body = { name: name.value.trim(), material_id: idNum(mat.value), machine_id: idNum(mac.value) };
        const j = await api.post('/jobs', body);
        location.hash = `#/jobs/${encodeURIComponent(j.id)}`;
      });
    } },
    field(t('job_name'), name), field(t('material'), mat), field(t('machine'), mac),
    h('div', { class: 'row end span-all' },
      h('button', { class: 'btn', type: 'button', onclick: () => formBox.classList.add('hidden') }, t('cancel')), create));
    formBox.replaceChildren(h('h2', {}, t('new_job')), form);
    name.focus();
  };

  const rows = jobs.map((j) => h('tr', { class: 'clickable', testid: 'job-row', 'data-id': j.id, tabindex: '0',
    onclick: () => { location.hash = `#/jobs/${encodeURIComponent(j.id)}`; },
    onkeydown: (e) => { if (e.key === 'Enter') location.hash = `#/jobs/${encodeURIComponent(j.id)}`; } },
  h('td', {}, h('strong', {}, j.name)),
  h('td', {}, nameOf(j.material)),
  h('td', {}, nameOf(j.machine)),
  h('td', {}, h('span', { class: `badge status-${j.status}` }, t(`status_${j.status}`, {}, j.status))),
  h('td', { class: 'num' }, j.sheets ?? ''),
  h('td', { class: 'num' }, j.utilisation != null && j.utilisation !== '' ? pct(j.utilisation) : ''),
  h('td', { class: 'muted' }, dateText(j.created_at))));

  const filter = h('div', { class: 'seg', role: 'tablist' },
    h('button', { class: `seg-btn${showBin ? '' : ' active'}`, role: 'tab', testid: 'jobs-filter-active', 'aria-selected': String(!showBin),
      onclick: () => { if (showBin) { showBin = false; again(); } } }, t('active_jobs')),
    h('button', { class: `seg-btn${showBin ? ' active' : ''}`, role: 'tab', testid: 'jobs-filter-bin', 'aria-selected': String(showBin),
      onclick: () => { if (!showBin) { showBin = true; again(); } } }, t('bin')));

  let list;
  if (showBin) {
    const binRows = jobs.map((j) => {
      const restore = h('button', { class: 'btn', testid: 'job-restore' }, t('restore'));
      restore.addEventListener('click', () => busy(restore, async () => {
        await api.post(`/jobs/${encodeURIComponent(j.id)}/restore`, {});
        toast(t('restored'), 'ok');
        await again();
      }));
      const purge = h('button', { class: 'btn danger-text', testid: 'job-purge' }, t('delete_forever'));
      purge.addEventListener('click', async () => {
        if (!(await confirmDialog(t('confirm_purge', { name: j.name }), { ok: t('delete_forever'), danger: true }))) return;
        await busy(purge, async () => {
          await api.del(`/jobs/${encodeURIComponent(j.id)}?purge=1`);
          toast(t('deleted'), 'ok');
          await again();
        });
      });
      return h('tr', { testid: 'bin-row', 'data-id': j.id },
        h('td', {}, h('strong', {}, j.name)),
        h('td', {}, nameOf(j.material)),
        h('td', {}, h('span', { class: `badge status-${j.status}` }, t(`status_${j.status}`, {}, j.status))),
        h('td', { class: 'muted' }, dateText(j.deleted_at)),
        h('td', { class: 'muted' }, dateText(j.purge_at)),
        h('td', { class: 'actions nowrap' }, restore, purge));
    });
    list = [h('p', { class: 'muted small' }, t('bin_note')),
      h('div', { class: 'table-wrap' },
        h('table', { testid: 'bin-table' },
          h('thead', {}, h('tr', {}, ['job_name', 'material', 'status', 'deleted_on', 'purge_on', ''].map((k) => h('th', {}, k ? t(k) : '')))),
          h('tbody', {}, binRows))),
      jobs.length ? null : h('p', { class: 'empty' }, t('bin_empty'))];
  } else {
    list = [h('div', { class: 'table-wrap' },
      h('table', { testid: 'jobs-table' },
        h('thead', {}, h('tr', {}, ['job_name', 'material', 'machine', 'status', 'sheets', 'utilisation', 'created']
          .map((k) => h('th', { class: k === 'sheets' || k === 'utilisation' ? 'num' : '' }, t(k))))),
        h('tbody', {}, rows))),
    jobs.length ? null : h('p', { class: 'empty' }, t('no_jobs'))];
  }

  fill(root,
    h('div', { class: 'row between' }, h('h1', {}, t('nav_jobs')),
      h('div', { class: 'row' }, filter,
        h('button', { class: 'btn primary', testid: 'new-job', onclick: showForm }, '+ ', t('new_job')))),
    formBox,
    ...list,
  );
}

// Keep numeric ids numeric (backend may use int ids), strings otherwise.
export function idNum(v) {
  if (v === '' || v == null) return undefined;
  return /^\d+$/.test(String(v)) ? Number(v) : v;
}
