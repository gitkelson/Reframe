// Bounded thumbnail loader for the File timeline (R-153): only requested (visible / adjacent / browsed) ids are
// fetched, at most CONCURRENCY at a time; requests no longer wanted are aborted; decoded bitmaps live in an LRU
// with a fixed cap and are closed on eviction. A generation counter tells the server which queued jobs are stale.
import { fetchThumb } from './api.js';

const CONCURRENCY = 4;
const MAX_BITMAPS = 400;
const MAX_PLACEHOLDERS = 5000;

export function createThumbLoader({ onChange, concurrency = CONCURRENCY, maxBitmaps = MAX_BITMAPS, load = fetchThumb } = {}) {
  const bitmaps = new Map();       // id -> {bitmap, w, h}   (LRU: insertion order)
  const placeholders = new Map();  // id -> reason
  const inflight = new Map();      // id -> AbortController
  let queue = [];
  let gen = 0;
  let lastKey = '';
  const channels = new Map();
  let pendingNotify = false;

  function notify() {
    if (pendingNotify) return;
    pendingNotify = true;
    requestAnimationFrame(() => { pendingNotify = false; onChange?.(); });
  }

  function remember(id, value) {
    bitmaps.delete(id);
    bitmaps.set(id, value);
    while (bitmaps.size > maxBitmaps) {
      const [oldId, old] = bitmaps.entries().next().value;
      bitmaps.delete(oldId);
      try { old.bitmap.close?.(); } catch { /* already closed */ }
    }
  }

  /** {status:'ok', bitmap, w, h} | {status:'placeholder', reason} | null when not loaded (yet). */
  function get(id) {
    const b = bitmaps.get(id);
    if (b) {
      bitmaps.delete(id);
      bitmaps.set(id, b);
      return { status: 'ok', ...b };
    }
    const r = placeholders.get(id);
    return r ? { status: 'placeholder', reason: r } : null;
  }

  function pump() {
    while (inflight.size < concurrency && queue.length) {
      const id = queue.shift();
      if (bitmaps.has(id) || placeholders.has(id) || inflight.has(id)) continue;
      const ctl = new AbortController();
      inflight.set(id, ctl);
      load(id, gen, ctl.signal)
        .then(async (r) => {
          if (r.status === 'ok') {
            const bitmap = await createImageBitmap(r.blob);
            remember(id, { bitmap, w: bitmap.width, h: bitmap.height });
          } else if (r.status === 'placeholder') {
            placeholders.set(id, r.reason);
            if (placeholders.size > MAX_PLACEHOLDERS) placeholders.delete(placeholders.keys().next().value);
          } else {                                     // busy or cancelled server-side: ask again on a later frame
            lastKey = '';
            setTimeout(notify, 400);
            return;
          }
          notify();
        })
        .catch((e) => {
          if (e && e.name === 'AbortError') return;
          if (e && e.name !== 'TypeError') {          // decoding failed in the browser
            placeholders.set(id, 'unsupported');
            notify();
          }
        })
        .finally(() => {
          if (inflight.get(id) === ctl) inflight.delete(id);
          pump();
        });
    }
  }

  /** Replace one channel's wanted list (highest priority first); the loader serves the union of all channels.
   *  Requests for ids no longer wanted by any channel are aborted. */
  function want(ids, channel = 'main') {
    channels.set(channel, ids);
    const wanted = [];
    const seen = new Set();
    for (const list of channels.values()) {
      for (const id of list) {
        if (seen.has(id) || bitmaps.has(id) || placeholders.has(id)) continue;
        seen.add(id);
        wanted.push(id);
      }
    }
    const key = JSON.stringify(wanted);
    if (key === lastKey) return;
    lastKey = key;
    gen += 1;
    for (const [id, ctl] of inflight) if (!seen.has(id)) { ctl.abort(); inflight.delete(id); }
    queue = wanted.filter((id) => !inflight.has(id));
    pump();
  }

  /** Forget everything (root or date-mode change): bitmaps are released, pending requests aborted. */
  function clear() {
    gen += 1;
    lastKey = '';
    for (const ctl of inflight.values()) ctl.abort();
    inflight.clear();
    queue = [];
    for (const b of bitmaps.values()) { try { b.bitmap.close?.(); } catch { /* closed */ } }
    bitmaps.clear();
    placeholders.clear();
  }

  return { get, want, clear, stats: () => ({ bitmaps: bitmaps.size, inflight: inflight.size, queued: queue.length, gen }) };
}
