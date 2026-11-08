// App shell: top bar, hash router, language switch.
import { t, getLang, setLang, onLangChange } from './i18n.js';
import { h } from './ui.js';
import * as jobs from './views/jobs.js';
import * as job from './views/job.js';
import * as relief from './views/relief.js';
import * as offcuts from './views/offcuts.js';
import * as settings from './views/settings.js';

const NAV = [
  { href: '#/jobs', key: 'nav_jobs', match: /^jobs/ },
  { href: '#/relief', key: 'nav_relief', match: /^relief/ },
  { href: '#/offcuts', key: 'nav_offcuts', match: /^offcuts/ },
  { href: '#/settings', key: 'nav_settings', match: /^settings/ },
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

function renderTopbar() {
  const bar = document.getElementById('topbar');
  const path = location.hash.replace(/^#\/?/, '') || 'jobs';
  const langSel = h('select', { class: 'lang', testid: 'lang-switch', 'aria-label': t('language'),
    onchange: (e) => setLang(e.target.value) },
  h('option', { value: 'en' }, 'English'), h('option', { value: 'th' }, 'ไทย'));
  langSel.value = getLang();
  bar.replaceChildren(
    h('a', { class: 'brand', href: '#/jobs' }, 'Nest'),
    h('nav', { class: 'nav' }, NAV.map((n) =>
      h('a', { href: n.href, class: n.match.test(path) ? 'active' : '', testid: `nav-${n.key.slice(4)}` }, t(n.key)))),
    langSel,
  );
}

let cleanup = null;
let renderSeq = 0;
async function route() {
  renderTopbar();
  document.documentElement.lang = getLang();
  const { view, params } = resolve(location.hash);
  if (typeof cleanup === 'function') { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  const root = document.getElementById('view');
  const seq = ++renderSeq;
  root.replaceChildren(h('p', { class: 'muted' }, t('loading')));
  const c = await view.render(root, params, () => seq === renderSeq);
  if (seq === renderSeq) cleanup = c;
}

window.addEventListener('hashchange', route);
onLangChange(route);
if (!location.hash) location.replace('#/jobs');
route();
