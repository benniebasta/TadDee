// Background tasks (nest, relief): a progress bar with Cancel, and a poller.
import { api, apiError } from './api.js';
import { t } from './i18n.js';
import { h } from './ui.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ACTIVE = new Set(['queued', 'running']);
export const isActive = (task) => !!task && ACTIVE.has(task.status);

// Progress bar element for a panel. testid: e.g. "nest-progress" (Cancel: "nest-cancel").
export function taskBar(testid, cancelTestid) {
  const fill = h('div', { class: 'task-fill' });
  const text = h('span', { class: 'task-text', testid: `${testid}-text` });
  const cancel = h('button', { class: 'btn small', type: 'button', testid: cancelTestid }, t('cancel_task'));
  const el = h('div', { class: 'task-bar hidden', testid, role: 'status', 'aria-live': 'polite' },
    h('div', { class: 'task-track', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100' }, fill),
    h('div', { class: 'task-row' }, text, h('span', { class: 'grow' }), cancel));
  let current = null;
  cancel.addEventListener('click', async () => {
    if (!current) return;
    cancel.disabled = true;
    cancel.textContent = t('cancelling');
    try { await api.post(`/tasks/${current.id}/cancel`, {}); } catch { /* the poller shows the end state */ }
  });
  return {
    el,
    show(task) {
      const first = !current || current.id !== task.id;
      current = task;
      el.classList.remove('hidden');
      const p = Math.round((Number(task.progress) || 0) * 100);
      fill.style.width = `${task.status === 'queued' ? 0 : Math.max(3, p)}%`;
      el.querySelector('.task-track').setAttribute('aria-valuenow', String(p));
      el.classList.toggle('queued', task.status === 'queued');
      const base = task.status === 'queued' ? t('task_queued') : t('task_running', { p });
      const msg = task.status === 'running' && task.message && !/^(Running|Waiting in the queue)$/.test(task.message) ? ` · ${task.message}` : '';
      text.textContent = base + msg;
      if (first) { cancel.disabled = false; cancel.textContent = t('cancel_task'); }
    },
    hide() { current = null; el.classList.add('hidden'); },
  };
}

// Poll a task until it ends; returns the final task, or null when alive() turns false.
export async function follow(task, bar, alive = () => true) {
  let tk = task;
  let wait = 150;
  while (isActive(tk)) {
    bar?.show(tk);
    await sleep(wait);
    if (!alive()) return null;
    wait = tk.status === 'queued' ? 800 : 400;
    try {
      tk = await api.get(`/tasks/${tk.id}`);
    } catch (e) {
      if (e.code === 'network') continue;   // server restarting: keep trying
      throw e;
    }
  }
  bar?.hide();
  return tk;
}

// Error of a failed task, for the toast (keeps the server code for EN/TH).
export function taskError(task) {
  const r = task?.result || {};
  return apiError(task?.message || 'Failed', r.code || 'bad_input', r.status || 400);
}
