// Small shared UI helpers: element builder, toast, busy buttons, dialogs, formatting.
import { t, getLang, hasKey } from './i18n.js';

// h('div', {class: 'x', testid: 'y', onclick: fn}, child, ...) -> HTMLElement
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'testid') el.dataset.testid = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== 'string') el[k] = v; // e.g. checked, value, disabled
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Labelled form field: label text above the control.
export function field(label, control, hint) {
  return h('label', { class: 'field' }, h('span', { class: 'field-label' }, label), control,
    hint ? h('span', { class: 'field-hint' }, hint) : null);
}

// Select with [{value, label}] options.
export function select(attrs, options, value) {
  const s = h('select', attrs);
  for (const o of options) {
    const opt = h('option', { value: String(o.value) }, o.label);
    if (String(o.value) === String(value)) opt.selected = true;
    s.append(opt);
  }
  return s;
}

// ---- toast -----------------------------------------------------------------
let toastTimer = null;
export function toast(message, type = 'error') {
  const el = document.querySelector('[data-testid="toast"]');
  if (!el) return;
  el.textContent = message;
  el.className = `toast show ${type}`;
  el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, type === 'error' ? 7000 : 3500);
}
export const toastError = (e) => toast(errorText(e), 'error');

// Error text in the chosen language. The server writes English sentences plus
// a code; Thai shows the Thai text of the code (with the detail for input errors).
const DETAIL_CODES = new Set(['bad_input', 'not_found', 'conflict', 'too_large', 'backup_failed']);
export function errorText(e) {
  const msg = e?.message || String(e ?? '');
  const code = e?.code;
  if (!code || !hasKey(`err_${code}`)) return msg;
  if (getLang() === 'en') return msg || t(`err_${code}`);
  if (DETAIL_CODES.has(code)) return msg ? `${t(`err_${code}`)}: ${msg}` : t(`err_${code}`);
  return t(`err_${code}`) + (e.requestId && code === 'server_error' ? ` (${e.requestId})` : '');
}

// ---- busy buttons ----------------------------------------------------------
// Disable the button, show a spinner text while fn runs; errors go to the toast.
export async function busy(btn, fn, workingText) {
  if (btn.disabled) return undefined;
  const old = [...btn.childNodes];
  btn.disabled = true;
  btn.classList.add('busy');
  btn.replaceChildren(h('span', { class: 'spinner', 'aria-hidden': 'true' }), workingText || t('working'));
  try {
    return await fn();
  } catch (e) {
    toastError(e);
    return undefined;
  } finally {
    if (btn.isConnected) {
      btn.disabled = false;
      btn.classList.remove('busy');
      btn.replaceChildren(...old);
    }
  }
}

// ---- in-page dialogs -------------------------------------------------------
// Confirm dialog; resolves true when OK is pressed. Never window.confirm.
export function confirmDialog(message, { ok = t('ok'), cancel = t('cancel'), danger = false } = {}) {
  return new Promise((resolve) => {
    const close = (v) => { back.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(false); };
    const okBtn = h('button', { class: danger ? 'btn danger' : 'btn primary', testid: 'confirm-ok', onclick: () => close(true) }, ok);
    const box = h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', testid: 'confirm-dialog' },
      h('p', {}, message),
      h('div', { class: 'row end' },
        h('button', { class: 'btn', testid: 'confirm-cancel', onclick: () => close(false) }, cancel), okBtn));
    const back = h('div', { class: 'backdrop', onclick: (e) => { if (e.target === back) close(false); } }, box);
    document.body.append(back);
    document.addEventListener('keydown', onKey);
    okBtn.focus();
  });
}

// Large view overlay (sheet zoom). Click anywhere or Esc to close.
export function overlay(content, title) {
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const back = h('div', { class: 'backdrop zoom', testid: 'zoom-overlay', onclick: close },
    h('div', { class: 'zoom-box' },
      h('div', { class: 'row between' }, h('strong', {}, title || ''),
        h('button', { class: 'btn', onclick: close }, t('close'))),
      h('div', { class: 'zoom-body' }, content)));
  document.body.append(back);
  document.addEventListener('keydown', onKey);
}

// ---- formatting ------------------------------------------------------------
export function num(v, digits = 0) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  return n.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}
export function money(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
// Utilisation may come as 0..1 or as a percent; show "62.5 %".
export function pct(u) {
  const n = Number(u);
  if (!Number.isFinite(n)) return '';
  return `${num(n <= 1.0001 ? n * 100 : n, 1)} %`;
}
// Field may be an object {name} or a plain string/id.
export const nameOf = (x) => (x && typeof x === 'object' ? x.name ?? '' : x ?? '');
export const idOf = (x) => (x && typeof x === 'object' ? x.id : x);
export function dateText(s) {
  if (!s) return '';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s);
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Insert a server SVG string into a box and make it scale to the box width.
export function svgBox(svgText, cls = 'svg-box') {
  const box = h('div', { class: cls });
  box.innerHTML = svgText || '';
  const svg = box.querySelector('svg');
  if (svg) {
    // keep viewBox, drop fixed size so CSS can scale it
    if (!svg.getAttribute('viewBox')) {
      const w = parseFloat(svg.getAttribute('width')), hh = parseFloat(svg.getAttribute('height'));
      if (w && hh) svg.setAttribute('viewBox', `0 0 ${w} ${hh}`);
    }
    svg.removeAttribute('width');
    svg.removeAttribute('height');
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  }
  return box;
}

// Simple debounce.
export function debounce(fn, ms) {
  let tm = null;
  return (...a) => { clearTimeout(tm); tm = setTimeout(() => fn(...a), ms); };
}

// Read number from an input; empty -> undefined.
export function numVal(input) {
  const s = String(input.value ?? '').trim();
  if (s === '') return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
}

// replaceChildren that skips null/false (plain replaceChildren would print "null").
export function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
  return el;
}

// ---- icons -------------------------------------------------------------------
// Our own simple line icons (24x24, stroke = currentColor).
const ICONS = {
  jobs: 'M4 7h16M4 12h16M4 17h10',
  relief: 'M3 19l6-9 4 5 3-4 5 8z',
  offcuts: 'M4 4h10v6h6v10H4z',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1',
  upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
  nest: 'M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z',
  export: 'M12 4v12M7 11l5 5 5-5M4 20h16',
  parts: 'M12 3l8 4.5v9L12 21l-8-4.5v-9z',
  zoomin: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5M11 8v6M8 11h6',
  zoomout: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5M8 11h6',
  fit: 'M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  back: 'M15 5l-7 7 7 7',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5',
  moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z',
  chevron: 'M6 9l6 6 6-6',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12l5 5 9-10',
  image: 'M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9h.01',
  file: 'M6 3h8l4 4v14H6zM14 3v4h4',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  lock: 'M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3',
  restore: 'M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4',
  backup: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
};
export function icon(name, size = 20) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8'); svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('ico');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', ICONS[name] || ICONS.file);
  svg.append(p);
  return svg;
}

// Collapsible section of the right-hand properties panel (open by default).
export function panelSection({ title, iconName, step, open = true, testid }, ...body) {
  const d = h('details', { class: 'psec', open, testid },
    h('summary', {}, step ? h('span', { class: 'step' }, step) : iconName ? icon(iconName, 18) : null,
      h('span', { class: 'psec-title' }, title), h('span', { class: 'grow' }), icon('chevron', 16)),
    h('div', { class: 'psec-body' }, body));
  return d;
}

// Work canvas with wheel zoom (around the pointer), drag to pan and fit.
export function canvasView({ testid = 'canvas' } = {}) {
  const stage = h('div', { class: 'stage' });
  const el = h('div', { class: 'canvas', testid, tabindex: '0' }, stage);
  let z = 1, x = 0, y = 0;
  const apply = () => { stage.style.transform = `translate(${x}px, ${y}px) scale(${z})`; zoomLabel.textContent = `${Math.round(z * 100)} %`; };
  const zoomLabel = h('span', { class: 'zoom-label', testid: 'zoom-label' }, '100 %');
  const zoomAt = (nz, cx, cy) => {
    nz = Math.min(20, Math.max(0.2, nz));
    x = cx - (cx - x) * (nz / z); y = cy - (cy - y) * (nz / z); z = nz; apply();
  };
  const center = () => { const r = el.getBoundingClientRect(); return [r.width / 2, r.height / 2]; };
  el.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = el.getBoundingClientRect();
    zoomAt(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  let drag = null;
  el.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('button,a,input,select')) return;
    drag = { sx: e.clientX - x, sy: e.clientY - y, id: e.pointerId, moved: false };
  });
  el.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const nx = e.clientX - drag.sx, ny = e.clientY - drag.sy;
    if (!drag.moved && Math.hypot(nx - x, ny - y) < 3) return;
    if (!drag.moved) { drag.moved = true; el.setPointerCapture(drag.id); el.classList.add('panning'); }
    x = nx; y = ny; apply();
  });
  const end = () => { drag = null; el.classList.remove('panning'); };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  el.addEventListener('keydown', (e) => {
    if (e.key === '+' || e.key === '=') zoomAt(z * 1.25, ...center());
    else if (e.key === '-') zoomAt(z / 1.25, ...center());
    else if (e.key === '0') fit();
  });
  const fit = () => { z = 1; x = 0; y = 0; apply(); };
  const controls = h('div', { class: 'zoom-ctrls' },
    h('button', { class: 'tbtn', title: 'Zoom out (-)', 'aria-label': 'Zoom out', testid: 'zoom-out', onclick: () => zoomAt(z / 1.25, ...center()) }, icon('zoomout', 18)),
    zoomLabel,
    h('button', { class: 'tbtn', title: 'Zoom in (+)', 'aria-label': 'Zoom in', testid: 'zoom-in', onclick: () => zoomAt(z * 1.25, ...center()) }, icon('zoomin', 18)),
    h('button', { class: 'tbtn', title: 'Fit (0)', 'aria-label': 'Fit', testid: 'zoom-fit', onclick: fit }, icon('fit', 18)));
  return {
    el, controls,
    set(...kids) { fill(stage, ...kids); fit(); },
    fit,
  };
}

// Theme: light by default, optional dark workspace. Stored in localStorage.
const THEME_KEY = 'nest.theme';
export function getTheme() { try { return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'; } catch { return 'light'; } }
export function applyTheme(th = getTheme()) {
  document.documentElement.dataset.theme = th;
  try { localStorage.setItem(THEME_KEY, th); } catch { /* private mode */ }
}
