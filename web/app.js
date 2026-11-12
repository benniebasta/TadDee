// App shell: top bar, hash router, language switch.
import { t, getLang, setLang, onLangChange } from './i18n.js';
import { h, icon, getTheme, applyTheme } from './ui.js';
import * as jobs from './views/jobs.js';
import * as job from './views/job.js';
import * as relief from './views/relief.js';
import * as offcuts from './views/offcuts.js';
import * as settings from './views/settings.js';

const NAV = [
  { href: '#/jobs', key: 'nav_jobs', icon: 'jobs', match: /^jobs/ },
  { href: '#/relief', key: 'nav_relief', icon: 'relief', match: /^relief/ },
  { href: '#/offcuts', key: 'nav_offcuts', icon: 'offcuts', match: /^offcuts/ },
  { href: '#/settings', key: 'nav_settings', icon: 'settings', match: /^settings/ },
];

// Route table: pattern -> view module.
function resolve(hash) {
  const path = hash.replace(/^#\/?/, '');
  let m;
  if ((m = path.match(/^jobs\/([^/]+)$/))) return { view: job, params: { id: decodeURIComponent(m[1]) }, path };
  if (/^jobs\/?$/.test(path) || path === '') return { view: jobs, params: {}, path: 'jobs' };
  if (/^relief\/?$/.test(path)) return { view: relief, params: {}, path };
  if (/^offcuts\/?$/.test(path)) return { view: offcuts, params: {}, path };
  if (/^settings\/?$/.test(path)) return { view: settings, params: {}, path };
  return { view: jobs, params: {}, path: 'jobs' };
}

// Left tool rail (bottom bar on phones): brand, sections, language, theme.
function renderRail() {
  const bar = document.getElementById('rail');
  const path = location.hash.replace(/^#\/?/, '') || 'jobs';
  const langSel = h('select', { class: 'lang', testid: 'lang-switch', 'aria-label': t('language'),
    onchange: (e) => setLang(e.target.value) },
  h('option', { value: 'en' }, 'EN'), h('option', { value: 'th' }, 'ไทย'));
  langSel.value = getLang();
  const dark = getTheme() === 'dark';
  const themeBtn = h('button', { class: 'rail-btn', testid: 'theme-switch', title: t(dark ? 'theme_light' : 'theme_dark'),
    'aria-label': t(dark ? 'theme_light' : 'theme_dark'),
    onclick: () => { applyTheme(getTheme() === 'dark' ? 'light' : 'dark'); renderRail(); } }, icon(dark ? 'sun' : 'moon', 18));
  bar.replaceChildren(
    h('a', { class: 'rail-brand', href: '#/jobs', title: 'TadDee' }, h('span', { class: 'logo' }, 'T')),
    ...NAV.map((n) => h('a', { href: n.href, class: `rail-item${n.match.test(path) ? ' active' : ''}`,
      testid: `nav-${n.key.slice(4)}`, 'aria-current': n.match.test(path) ? 'page' : null },
    icon(n.icon, 22), h('span', {}, t(n.key)))),
    h('div', { class: 'rail-foot' }, langSel, themeBtn),
  );
}

let cleanup = null;
let renderSeq = 0;
async function route() {
  renderRail();
  document.documentElement.lang = getLang();
  const { view, params, path } = resolve(location.hash);
  if (typeof cleanup === 'function') { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  const root = document.getElementById('view');
  // job and relief pages are full-screen workspaces; the rest are scrolling pages
  root.classList.toggle('ws-mode', view === job || view === relief);
  root.scrollTop = 0;
  const seq = ++renderSeq;
  root.replaceChildren(h('p', { class: 'muted' }, t('loading')));
  const c = await view.render(root, params, () => seq === renderSeq);
  if (seq === renderSeq) cleanup = c;
}

applyTheme();
window.addEventListener('hashchange', route);
onLangChange(route);
if (!location.hash) location.replace('#/jobs');
route();
