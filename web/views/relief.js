// Relief page (#/relief): picture -> 3D relief G-code (rough + finish), editable styles.
import { api, url } from '../api.js';
import { t } from '../i18n.js';
import { h, fill, field, select, busy, toast, num, numVal } from '../ui.js';

const PROFILES = ['dome', 'flat', 'bevel', 'engrave', 'background'];

// Bit choices as the relief module writes them: "<dia>flat" | "<dia>ball".
function bitOptions(tools, kind) {
  const set = new Set();
  for (const x of tools) {
    if (x.kind === kind && Number(x.diameter_mm) > 0) set.add(`${Number(x.diameter_mm)}${kind}`);
  }
  if (!set.size) (kind === 'flat' ? ['6flat', '3flat'] : ['3ball']).forEach((v) => set.add(v));
  return [...set];
}

export async function render(root, _params, isCurrent) {
  let tools = [];
  try { tools = await api.get('/tools'); } catch { /* fall back to built-in bit list */ }
  if (!isCurrent()) return;

  const S = { result: null };
  const flats = bitOptions(tools, 'flat'), balls = bitOptions(tools, 'ball');
  const roughOpts = [...flats, ...balls].map((v) => ({ value: v, label: bitLabel(v) }));
  roughOpts.push({ value: 'none', label: t('no_rough') });
  const finishOpts = [...balls, ...flats].map((v) => ({ value: v, label: bitLabel(v) }));

  const image = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/bmp,image/gif,image/webp,.png,.jpg,.jpeg,.bmp',
    testid: 'relief-image', class: 'file' });
  const width = h('input', { type: 'number', min: '10', step: '1', value: '600', testid: 'relief-width', inputmode: 'decimal' });
  const material = h('input', { type: 'number', min: '1', step: '0.5', value: '18', testid: 'relief-material', inputmode: 'decimal' });
  const height = h('input', { type: 'number', min: '0.5', step: '0.5', value: '6', testid: 'relief-height', inputmode: 'decimal' });
  const rough = select({ testid: 'relief-rough' }, roughOpts, roughOpts.find((o) => o.value === '6flat') ? '6flat' : roughOpts[0].value);
  const finish = select({ testid: 'relief-finish' }, finishOpts, finishOpts.find((o) => o.value === '3ball') ? '3ball' : finishOpts[0].value);
  const runBtn = h('button', { class: 'btn primary big', testid: 'relief-run' }, t('make_relief'));
  const result = h('div', { class: 'relief-result' });

  const params = () => ({
    width_mm: numVal(width), material_mm: numVal(material), max_height_mm: numVal(height),
    rough: rough.value, finish: finish.value,
  });

  runBtn.addEventListener('click', () => busy(runBtn, async () => {
    const f = image.files?.[0];
    if (!f) throw new Error(t('err_pick_image'));
    const p = params();
    if (!p.width_mm || !p.material_mm) throw new Error(t('err_relief_sizes'));
    const fd = new FormData();
    fd.append('image', f, f.name);
    for (const [k, v] of Object.entries(p)) if (v !== undefined) fd.append(k, String(v));
    S.result = await api.upload('/relief', fd);
    renderResult();
  }, t('making_relief')));

  // Region style rows (kept so Update can read them).
  let rows = [];
  function renderResult() {
    const r = S.result;
    if (!r) { result.replaceChildren(); return; }
    const bust = `?v=${Date.now()}`;
    const imgUrls = imageUrls(r);
    const preview = imgUrls.find((u) => /preview/i.test(u)) || imgUrls[0];

    rows = (r.regions || []).map((g) => {
      const prof = select({ testid: 'region-profile', 'aria-label': t('profile') },
        PROFILES.map((p) => ({ value: p, label: t(`prof_${p}`) })), g.profile);
      const hIn = h('input', { type: 'number', step: '0.5', value: String(g.height_mm ?? 0), testid: 'region-height', class: 'small-in', inputmode: 'decimal' });
      const rIn = h('input', { type: 'number', min: '0', step: '0.5', value: String(g.round_mm ?? 0), testid: 'region-round', class: 'small-in', inputmode: 'decimal' });
      const tr = h('tr', { testid: 'relief-region-row' },
        h('td', {}, h('span', { class: 'swatch', style: `background:${g.color}`, title: g.color })),
        h('td', { class: 'muted small' }, g.color),
        h('td', { class: 'num' }, num((g.area_mm2 ?? 0) / 100, 0)),
        h('td', {}, prof), h('td', {}, hIn), h('td', {}, rIn));
      return { g, prof, hIn, rIn, tr };
    });

    const updBtn = h('button', { class: 'btn primary', testid: 'relief-update' }, t('update'));
    updBtn.addEventListener('click', () => busy(updBtn, async () => {
      const styles = rows.map(({ g, prof, hIn, rIn }) => ({
        index: g.index, profile: prof.value, height_mm: numVal(hIn) ?? 0, round_mm: numVal(rIn) ?? 0,
        ...(g.base_mm !== undefined ? { base_mm: g.base_mm } : {}) }));
      const p = params();
      for (const k of Object.keys(p)) if (p[k] === undefined) delete p[k];
      S.result = await api.post(`/relief/${encodeURIComponent(r.id)}`, { ...p, styles });
      renderResult();
      toast(t('relief_updated'), 'ok');
    }, t('making_relief')));

    const files = (r.files || []).map((f, i) => h('div', { class: 'file' },
      h('a', { href: fileUrl(r, f.name, i), download: f.name, testid: 'relief-file' }, f.name),
      h('span', { class: 'muted small' }, [f.tool, f.minutes != null ? t('minutes_n', { n: num(f.minutes, 1) }) : null,
        f.deepest_z != null ? `Z ${num(f.deepest_z, 2)}` : null].filter(Boolean).join(' · '))));
    const extra = (r.images || []).map((name, i) => h('a', { class: 'small', href: imgUrls[i] || relUrl(r, name), download: name }, name));

    fill(result, 
      h('div', { class: 'summary' },
        statBox(t('size'), `${num(r.width_mm, 1)} × ${num(r.height_mm, 1)} mm`),
        statBox(t('depth'), `${num(r.depth_mm, 2)} mm`),
        statBox(t('lost_detail'), `${num(r.lost_detail_mm, 2)} mm`),
        statBox(t('total_time'), t('minutes_n', { n: num(r.total_minutes, 1) }))),
      preview ? h('img', { class: 'relief-img', testid: 'relief-preview', src: preview + bust, alt: t('preview') }) : null,
      h('h3', {}, t('regions')),
      h('div', { class: 'table-wrap' }, h('table', { testid: 'relief-regions' },
        h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, t('colour')), h('th', { class: 'num' }, t('area_cm2')),
          h('th', {}, t('profile')), h('th', {}, t('height_mm')), h('th', {}, t('round_mm')))),
        h('tbody', {}, rows.map((x) => x.tr)))),
      h('div', { class: 'row' }, updBtn),
      h('h3', {}, t('files')),
      h('div', { class: 'files', testid: 'relief-files' }, files,
        h('p', { class: 'muted', testid: 'relief-total' }, t('total_time'), ': ', t('minutes_n', { n: num(r.total_minutes, 1) }))),
      extra.length ? h('div', { class: 'row wrap small-gap' }, h('span', { class: 'muted small' }, t('images'), ':'), extra) : null);
  }

  root.replaceChildren(
    h('h1', {}, t('nav_relief')),
    h('p', { class: 'muted' }, t('relief_intro')),
    h('section', { class: 'card' },
      h('div', { class: 'grid-form' },
        field(t('picture'), image, t('picture_hint')),
        field(t('width_mm'), width),
        field(t('material_mm'), material),
        field(t('max_height_mm'), height),
        field(t('rough_bit'), rough),
        field(t('finish_bit'), finish)),
      h('div', { class: 'row' }, runBtn)),
    h('section', { class: 'card' }, result));
  renderResult();
}

function bitLabel(v) {
  const m = String(v).match(/^([\d.]+)(flat|ball)$/);
  return m ? `${m[1]} mm ${t(m[2] === 'flat' ? 'kind_flat' : 'kind_ball')}` : v;
}
function relUrl(r, name) { return url(`/relief/${encodeURIComponent(r.id)}/files/${encodeURIComponent(name)}`); }
// image_urls may be a list of urls or {name: url}; fall back to the files endpoint.
function imageUrls(r) {
  const iu = r.image_urls;
  return (r.images || []).map((name, i) => {
    if (Array.isArray(iu)) return iu.find((u) => String(u).endsWith(name)) || iu[i] || relUrl(r, name);
    if (iu && typeof iu === 'object') return iu[name] || relUrl(r, name);
    return relUrl(r, name);
  }).concat(Array.isArray(iu) && !(r.images || []).length ? iu : []);
}
function fileUrl(r, name, i) {
  const fu = r.file_urls;
  const f = (r.files || [])[i];
  if (f && f.url) return f.url;
  if (Array.isArray(fu)) return fu.find((u) => String(u).endsWith(name)) || fu[i] || relUrl(r, name);
  if (fu && typeof fu === 'object') return fu[name] || relUrl(r, name);
  return relUrl(r, name);
}
function statBox(label, value) {
  return h('div', { class: 'stat' }, h('span', { class: 'stat-label' }, label), h('span', { class: 'stat-value' }, value));
}
