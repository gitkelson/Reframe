// Theme access and text measurement for the canvas modules. Colors are never hard-coded: they are read from
// the CSS custom properties of the active theme (css/tokens-*.css) and re-read on the store's 'theme' event.

const TOKENS = [
  'canvas-bg', 'weekend-shade', 'today-shade', 'grid-line', 'grid-line-strong', 'text', 'text-secondary', 'ruler-bg', 'ruler-text',
  'ruler-text-dim', 'ruler-line', 'now-line', 'now-line-on-ruler', 'minimap-bg', 'minimap-bar', 'minimap-frame',
  'minimap-frame-fill', 'focus-ring', 'bar-selected-outline', 'bar-untagged', 'bar-untagged-fg', 'bar-log-outline',
  'drag-ghost', 'marquee-fill', 'marquee-stroke', 'highlight-external', 'lane-separator', 'drop-target',
  'lane-header-bg', 'lane-header-text', 'today-marker', 'hal-eye', 'hal-glow', 'monolith', 'monolith-glow',
];

export function readTheme() {
  const cs = getComputedStyle(document.documentElement);
  const get = (name) => cs.getPropertyValue(`--${name}`).trim();
  const theme = { fontUi: get('font-ui') || 'sans-serif', fontMono: get('font-mono') || 'monospace', patterns: new Map() };
  for (const t of TOKENS) theme[t] = get(t) || 'currentColor';
  theme.radius = Math.min(4, (parseFloat(get('radius')) || 0) / 3);
  theme.tags = Array.from({ length: 8 }, (_, i) => ({ bg: get(`tag-${i}`) || theme['bar-untagged'], fg: get(`tag-${i}-fg`) || theme['bar-untagged-fg'] }));
  return theme;
}

/** Bar style for a tag slot (0-15): slots 8-15 are the same hue as slot-8 but hatched. Untagged: neutral. */
export function slotStyle(theme, slot) {
  if (!Number.isInteger(slot) || slot < 0 || slot > 15) {
    return { fill: theme['bar-untagged'], fg: theme['bar-untagged-fg'], hatched: false, hue: -1 };
  }
  const hue = slot % 8;
  return { fill: theme.tags[hue].bg, fg: theme.tags[hue].fg, hatched: slot >= 8, hue };
}

export function itemStyle(theme, item, tags) {
  const primary = item.tags && item.tags.length ? tags[item.tags[0]] : null;
  return slotStyle(theme, primary ? primary.slot : null);
}

/** Diagonal hatch: the hue as base with stripes in its label color. Cached per theme read and pixel ratio. */
export function hatchPattern(ctx, theme, hue, dpr) {
  const key = `${hue}|${dpr}`;
  let pat = theme.patterns.get(key);
  if (pat) return pat;
  const size = Math.max(4, Math.round(8 * dpr));
  const tile = document.createElement('canvas');
  tile.width = tile.height = size;
  const t = tile.getContext('2d');
  const { bg, fg } = hue < 0 ? { bg: theme['bar-untagged'], fg: theme['bar-untagged-fg'] } : theme.tags[hue];
  t.fillStyle = bg;
  t.fillRect(0, 0, size, size);
  t.strokeStyle = fg;
  t.globalAlpha = 0.5;
  t.lineWidth = Math.max(1, size / 6);
  t.beginPath();
  for (const k of [-1, 0, 1]) { t.moveTo(k * size, size); t.lineTo(k * size + size, 0); }
  t.stroke();
  pat = ctx.createPattern(tile, 'repeat');
  pat.setTransform(new DOMMatrix().scale(1 / dpr));
  theme.patterns.set(key, pat);
  return pat;
}

/** Text width in CSS pixels with a cache; owns a private context so it never disturbs drawing state. */
export function createMeasurer() {
  const ctx = document.createElement('canvas').getContext('2d');
  const cache = new Map();
  let font = '';
  return {
    setFont(f) { if (f !== font) { font = f; cache.clear(); } },
    measure(text) {
      let w = cache.get(text);
      if (w === undefined) {
        ctx.font = font;
        w = ctx.measureText(text).width;
        if (cache.size > 20000) cache.clear();
        cache.set(text, w);
      }
      return w;
    },
  };
}

let cssLoaded = false;
/** Loads css/canvas.css (tooltip, rename input, hidden live region). */
export function loadCanvasCss() {
  if (cssLoaded) return;
  cssLoaded = true;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/static/css/canvas.css';
  document.head.appendChild(link);
}
