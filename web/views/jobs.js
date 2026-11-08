// Jobs list (#/jobs) with a New job form.
import { api } from '../api.js';
import { t } from '../i18n.js';
import { h, fill, field, select, busy, toastError, pct, nameOf, dateText } from '../ui.js';

export async function render(root, _params, isCurrent) {
  let jobs = [], materials = [], machines = [];
  try {
    [jobs, materials, machines] = await Promise.all([api.get('/jobs'), api.get('/materials'), api.get('/machines')]);
  } catch (e) {
    toastError(e);
  }
  if (!isCurrent()) return;

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

  fill(root, 
    h('div', { class: 'row between' }, h('h1', {}, t('nav_jobs')),
      h('button', { class: 'btn primary', testid: 'new-job', onclick: showForm }, '+ ', t('new_job'))),
    formBox,
    h('div', { class: 'table-wrap' },
      h('table', { testid: 'jobs-table' },
        h('thead', {}, h('tr', {}, ['job_name', 'material', 'machine', 'status', 'sheets', 'utilisation', 'created']
          .map((k) => h('th', { class: k === 'sheets' || k === 'utilisation' ? 'num' : '' }, t(k))))),
        h('tbody', {}, rows))),
    jobs.length ? null : h('p', { class: 'empty' }, t('no_jobs')),
  );
}

// Keep numeric ids numeric (backend may use int ids), strings otherwise.
export function idNum(v) {
  if (v === '' || v == null) return undefined;
  return /^\d+$/.test(String(v)) ? Number(v) : v;
}
