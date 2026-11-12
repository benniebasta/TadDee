// Web Worker for the G-code simulator: parses the text off the main thread so a
// relief file with hundreds of thousands of lines never freezes the page.
// Message in:  { text, opts }      Message out: { type: 'progress', done, total } ... { type: 'done', result }
import { createParser, transferList } from './sim_parse.js';

const CHUNK = 1 << 20;   // 1 MB of text per step

self.onmessage = (e) => {
  const { text, opts } = e.data || {};
  try {
    const total = text.length;
    let lastPost = 0;
    const p = createParser({
      ...(opts || {}),
      onProgress(done) {
        const now = Date.now();
        if (now - lastPost > 80) { lastPost = now; self.postMessage({ type: 'progress', done, total }); }
      },
    });
    for (let i = 0; i < total; i += CHUNK) p.push(text.slice(i, i + CHUNK));
    const result = p.end();
    self.postMessage({ type: 'done', result }, transferList(result));
  } catch (err) {
    self.postMessage({ type: 'error', message: String(err && err.message || err) });
  }
};
