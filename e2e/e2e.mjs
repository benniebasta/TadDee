// End-to-end test of the Nest app through the browser.
// Usage: start the app (NEST_DATA_DIR=<empty dir> python -m app), then
//   node e2e/e2e.mjs [baseUrl]
// Uses the globally installed Playwright + Chromium. Screenshots go to e2e/results/.
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const out = path.join(here, 'results');
fs.mkdirSync(out, { recursive: true });
const BASE = process.argv[2] || 'http://127.0.0.1:8040';

const results = [];
const consoleErrors = [];
let shot = 0;

async function step(name, fn, page) {
  const t0 = Date.now();
  try {
    await fn();
    results.push({ name, ok: true, ms: Date.now() - t0 });
    console.log(`PASS ${name} (${Date.now() - t0} ms)`);
  } catch (e) {
    results.push({ name, ok: false, error: String(e.message || e).split('\n')[0] });
    console.log(`FAIL ${name}: ${e.message}`);
  }
  if (page) await page.screenshot({ path: path.join(out, `${String(++shot).padStart(2, '0')}-${name.replace(/\W+/g, '-')}.png`), fullPage: true }).catch(() => {});
}

const tid = (page, id) => page.getByTestId(id);
function expect(cond, msg) { if (!cond) throw new Error(msg); }

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
page.setDefaultTimeout(30000);
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', (e) => consoleErrors.push(String(e)));

let jobUrl = '';

await step('app loads with jobs page', async () => {
  const r = await page.request.get(`${BASE}/api/health`);
  expect(r.ok(), 'health not ok');
  await page.goto(`${BASE}/#/jobs`);
  await tid(page, 'jobs-table').waitFor();
  await tid(page, 'new-job').waitFor();
}, page);

await step('settings: add a tool', async () => {
  await page.goto(`${BASE}/#/settings`);
  await tid(page, 'settings-tools').waitFor();
  const before = await tid(page, 'settings-tools').locator('tbody tr').count();
  await tid(page, 'tool-add').click();
  await tid(page, 'tool-name').fill('E2E 4 mm flat');
  await tid(page, 'tool-diameter').fill('4');
  await tid(page, 'tool-kind').selectOption('flat');
  await tid(page, 'tool-save').click();
  await page.waitForFunction((n) => document.querySelectorAll('[data-testid="settings-tools"] tbody tr').length > n, before);
  const api = await (await page.request.get(`${BASE}/api/tools`)).json();
  expect(api.some((t) => t.name === 'E2E 4 mm flat' && Number(t.diameter_mm) === 4), 'tool not saved');
}, page);

await step('settings: set a material price', async () => {
  const mats = await (await page.request.get(`${BASE}/api/materials`)).json();
  const acr = mats.find((m) => /acrylic/i.test(m.name));
  expect(acr, 'no acrylic material seeded');
  // edit through the UI: click the row's edit control, then fill the price
  const row = page.locator('tr', { hasText: acr.name }).first();
  await row.getByRole('button', { name: /edit|แก้ไข/i }).first().click();
  await tid(page, 'material-price').fill('1500');
  await tid(page, 'material-save').click();
  await page.waitForTimeout(500);
  const after = await (await page.request.get(`${BASE}/api/materials/${acr.id}`).catch(() => null))?.json?.()
    ?? (await (await page.request.get(`${BASE}/api/materials`)).json()).find((m) => m.id === acr.id);
  const m2 = Array.isArray(after) ? after.find((m) => m.id === acr.id) : after;
  expect(Number(m2.price_per_sheet) === 1500, `price is ${m2.price_per_sheet}`);
}, page);

await step('create a CNC job', async () => {
  await page.goto(`${BASE}/#/jobs`);
  await tid(page, 'new-job').click();
  await tid(page, 'job-name').fill('E2E acrylic letters');
  const mat = tid(page, 'job-material');
  const matOpt = await mat.locator('option', { hasText: /Acrylic/i }).first().getAttribute('value');
  await mat.selectOption(matOpt);
  const mac = tid(page, 'job-machine');
  const macOpt = await mac.locator('option', { hasText: /Router|NC Studio/i }).first().getAttribute('value');
  await mac.selectOption(macOpt);
  await tid(page, 'job-create').click();
  await page.waitForURL(/#\/jobs\/[^/]+$/);
  jobUrl = page.url();
  await tid(page, 'file-input').waitFor({ state: 'attached' });
}, page);

await step('upload SVG and DXF parts', async () => {
  await tid(page, 'file-input').setInputFiles([
    path.join(root, 'samples/parts.svg'), path.join(root, 'samples/letters.svg'), path.join(root, 'samples/parts.dxf'),
  ]);
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="part-row"]').length >= 5, null, { timeout: 30000 });
  const n = await tid(page, 'part-row').count();
  expect(n >= 5, `only ${n} parts`);
  const thumbs = await page.locator('[data-testid="part-row"] svg').count();
  expect(thumbs >= n, 'part thumbnails missing');
}, page);

await step('change quantity and delete a part', async () => {
  const first = tid(page, 'part-row').first();
  await first.getByTestId('part-qty').fill('3');
  await first.getByTestId('part-qty').dispatchEvent('change');
  await page.waitForTimeout(900);
  const id = jobUrl.split('/').pop();
  let job = await (await page.request.get(`${BASE}/api/jobs/${id}`)).json();
  expect(job.parts.some((p) => p.qty === 3), 'qty 3 not saved');
  const before = job.parts.length;
  await tid(page, 'part-delete').last().click();
  await page.waitForFunction((n) => document.querySelectorAll('[data-testid="part-row"]').length === n - 1, before);
  job = await (await page.request.get(`${BASE}/api/jobs/${id}`)).json();
  expect(job.parts.length === before - 1, 'part not deleted');
}, page);

await step('nest on sheets', async () => {
  await tid(page, 'nest-quality').selectOption({ index: 0 }); // Fast
  await tid(page, 'nest-run').click();
  await tid(page, 'sheet-preview').first().waitFor({ timeout: 60000 });
  const summary = await tid(page, 'nest-summary').innerText();
  expect(/%/.test(summary), `summary has no %: ${summary}`);
  expect(/VAT/i.test(summary) || /ภาษี/.test(summary), 'cost not labelled excl. VAT');
  const svgParts = await page.locator('[data-testid="sheet-preview"] svg [data-part-id]').count();
  expect(svgParts >= 5, `only ${svgParts} parts drawn on sheets`);
  const id = jobUrl.split('/').pop();
  const job = await (await page.request.get(`${BASE}/api/jobs/${id}`)).json();
  expect(job.status === 'nested', `status ${job.status}`);
  expect(job.nest && job.nest.sheets_used >= 1, 'no sheets in nest');
}, page);

await step('sheet preview zooms', async () => {
  await tid(page, 'sheet-preview').first().click();
  await page.waitForTimeout(400);
  await page.keyboard.press('Escape');
}, page);

await step('export NC Studio G-code', async () => {
  // the letters have inside corners tighter than the 6 mm bit: the checks list them
  await tid(page, 'export-check').click();
  await tid(page, 'export-issue').first().waitFor({ timeout: 60000 });
  // ...and the export needs them accepted
  await tid(page, 'export-accept-warnings').check();
  await tid(page, 'export-ncstudio').click();
  await tid(page, 'export-file').first().waitFor({ timeout: 60000 });
  const links = await tid(page, 'export-file').evaluateAll((as) => as.map((a) => a.getAttribute('href')));
  expect(links.length >= 1 && links.every((h) => /\.nc$/i.test(h)), `bad links ${links}`);
  const txt = await (await page.request.get(new URL(links[0], BASE).toString())).text();
  expect(txt.includes('G21 G90 G17') && /M30\s*$/.test(txt), 'G-code header/footer wrong');
  expect(!/M0?6\b/.test(txt) && !/\bT\d/.test(txt), 'tool change found');
  const zs = [...txt.matchAll(/Z(-?\d+(?:\.\d+)?)/g)].map((m) => Number(m[1]));
  expect(Math.min(...zs) >= -3.31 && Math.min(...zs) <= -3.29, `deepest Z ${Math.min(...zs)} (expected -3.3 on 3 mm acrylic)`);
  const zip = await page.request.get(new URL(await tid(page, 'export-zip').getAttribute('href'), BASE).toString());
  expect(zip.ok() && (await zip.body()).subarray(0, 2).toString() === 'PK', 'zip broken');
}, page);

await step('switch job to laser and export Ruida DXF', async () => {
  const id = jobUrl.split('/').pop();
  const machines = await (await page.request.get(`${BASE}/api/machines`)).json();
  const laser = machines.find((m) => m.kind === 'laser');
  // change machine through the job page select if present, else via API
  const sel = page.locator('select[data-testid="job-machine"]');
  if (await sel.count()) await sel.selectOption(String(laser.id));
  else await page.request.patch(`${BASE}/api/jobs/${id}`, { data: { machine_id: laser.id } });
  await page.reload();
  // wait for the re-nest itself: the old preview is still on screen, and the old
  // layout (bigger than the laser bed) is refused by the export checks
  const nested = page.waitForResponse((r) => r.url().endsWith('/nest') && r.request().method() === 'POST', { timeout: 60000 });
  await tid(page, 'nest-run').click();
  await nested;
  await tid(page, 'sheet-preview').first().waitFor({ timeout: 60000 });
  await tid(page, 'export-ruida').click();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="export-file"]')].some((a) => /\.dxf$/i.test(a.getAttribute('href'))), null, { timeout: 60000 });
  const href = await page.locator('[data-testid="export-file"][href$=".dxf"]').first().getAttribute('href');
  const dxf = await (await page.request.get(new URL(href, BASE).toString())).text();
  expect(dxf.includes('CUT') && dxf.includes('LWPOLYLINE'), 'DXF missing CUT layer or polylines');
}, page);

await step('mark as cut saves offcut', async () => {
  await tid(page, 'job-commit').click();
  await tid(page, 'confirm-ok').click();
  await page.waitForTimeout(800);
  const id = jobUrl.split('/').pop();
  const job = await (await page.request.get(`${BASE}/api/jobs/${id}`)).json();
  expect(job.status === 'cut', `status ${job.status}`);
  await page.goto(`${BASE}/#/offcuts`);
  await tid(page, 'offcuts-table').waitFor();
  const rows = await tid(page, 'offcuts-table').locator('tbody tr').count();
  expect(rows >= 1, 'no offcut saved');
}, page);

await step('second job reuses the offcut', async () => {
  await page.goto(`${BASE}/#/jobs`);
  await tid(page, 'new-job').click();
  await tid(page, 'job-name').fill('E2E offcut reuse');
  const mat = tid(page, 'job-material');
  await mat.selectOption(await mat.locator('option', { hasText: /Acrylic/i }).first().getAttribute('value'));
  const mac = tid(page, 'job-machine');
  await mac.selectOption(await mac.locator('option', { hasText: /Laser/i }).first().getAttribute('value'));
  await tid(page, 'job-create').click();
  await page.waitForURL(/#\/jobs\/[^/]+$/);
  await tid(page, 'file-input').setInputFiles([path.join(root, 'samples/parts.dxf')]);
  await tid(page, 'part-row').first().waitFor();
  const off = tid(page, 'nest-offcuts');
  if (!(await off.isChecked())) await off.check();
  await tid(page, 'nest-run').click();
  await tid(page, 'sheet-preview').first().waitFor({ timeout: 60000 });
  const id = page.url().split('/').pop();
  const job = await (await page.request.get(`${BASE}/api/jobs/${id}`)).json();
  expect(job.nest.sheets[0].is_offcut === true, 'first sheet is not the offcut');
}, page);

await step('jobs list shows both jobs', async () => {
  await page.goto(`${BASE}/#/jobs`);
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="job-row"]').length >= 2);
}, page);

await step('3D relief from a picture', async () => {
  await page.goto(`${BASE}/#/relief`);
  await tid(page, 'relief-image').setInputFiles(path.join(root, 'samples/sign.png'));
  await tid(page, 'relief-width').fill('200');
  await tid(page, 'relief-material').fill('18');
  await tid(page, 'relief-height').fill('5');
  await tid(page, 'relief-run').click();
  await tid(page, 'relief-preview').waitFor({ timeout: 120000 });
  await page.waitForFunction(() => { const i = document.querySelector('[data-testid="relief-preview"]'); return i && i.naturalWidth > 0; });
  const rows = await tid(page, 'relief-regions').locator('tbody tr').count();
  expect(rows >= 3, `only ${rows} regions`);
  const files = await page.locator('[data-testid="relief-files"] a').evaluateAll((as) => as.map((a) => a.getAttribute('href')));
  expect(files.filter((h) => /\.nc$/i.test(h)).length === 2, `relief files ${files}`);
}, page);

await step('relief: edit a height and update', async () => {
  const input = tid(page, 'relief-regions').locator('tbody tr').nth(1).locator('input[type="number"]').first();
  await input.fill('3');
  const before = await tid(page, 'relief-preview').getAttribute('src');
  await tid(page, 'relief-update').click();
  await page.waitForFunction((b) => document.querySelector('[data-testid="relief-preview"]')?.getAttribute('src') !== b, before, { timeout: 120000 });
}, page);

await step('Thai language switch', async () => {
  await page.goto(`${BASE}/#/jobs`);
  const sw = page.locator('[data-testid="lang-switch"], button:has-text("ไทย"), select:has(option:has-text("ไทย"))').first();
  if (await sw.evaluate((el) => el.tagName) === 'SELECT') await sw.selectOption({ label: 'ไทย' });
  else await sw.click();
  await page.waitForTimeout(300);
  const text = await page.locator('body').innerText();
  expect(/[฀-๿]/.test(text), 'no Thai text after switch');
}, page);

await step('phone layout renders', async () => {
  const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  const p = await m.newPage();
  await p.goto(jobUrl);
  await p.getByTestId('parts-table').waitFor();
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await p.screenshot({ path: path.join(out, `${String(++shot).padStart(2, '0')}-phone.png`), fullPage: true });
  await m.close();
  expect(overflow <= 2, `page is ${overflow}px wider than the phone screen`);
}, null);

await step('no browser console errors', async () => {
  const real = consoleErrors.filter((e) => !/favicon/i.test(e));
  expect(real.length === 0, real.slice(0, 5).join(' | '));
}, null);

await browser.close();
fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({ base: BASE, results, consoleErrors }, null, 2));
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} steps passed`);
process.exit(failed.length ? 1 : 0);
