// Small shared UI helpers: element builder, toast, busy buttons, dialogs, formatting.
import { t } from './i18n.js';

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
export const toastError = (e) => toast(e?.message || String(e), 'error');

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
