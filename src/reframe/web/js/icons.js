// Inline SVG icons (24x24 grid, stroke = currentColor), built with createElementNS (no markup strings).
const NS = 'http://www.w3.org/2000/svg';

const PATHS = {
  today: ['M4 6h16v14H4z', 'M4 10h16', 'M8 3v4', 'M16 3v4', 'M12 13.5v3.5h3'],
  zoomOut: ['M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z', 'M15.5 15.5 20 20', 'M7.5 10.5h6'],
  zoomIn: ['M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z', 'M15.5 15.5 20 20', 'M7.5 10.5h6', 'M10.5 7.5v6'],
  fit: ['M4 9V4h5', 'M20 9V4h-5', 'M4 15v5h5', 'M20 15v5h-5', 'M9 12h6'],
  group: ['M4 6h16', 'M4 12h16', 'M4 18h16', 'M7 4v4', 'M7 10v4', 'M7 16v4'],
  undo: ['M9 7 4 12l5 5', 'M4 12h10a6 6 0 0 1 0 12h-3'],
  redo: ['M15 7l5 5-5 5', 'M20 12H10a6 6 0 0 0 0 12h3'],
  sync: ['M20 11a8 8 0 0 0-14-4L4 9', 'M4 4v5h5', 'M4 13a8 8 0 0 0 14 4l2-2', 'M20 20v-5h-5'],
  panel: ['M4 5h16v14H4z', 'M14 5v14'],
  menu: ['M6 9l6 6 6-6'],
  plus: ['M12 5v14', 'M5 12h14'],
  close: ['M6 6l12 12', 'M18 6 6 18'],
  search: ['M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z', 'M15.5 15.5 20 20'],
  trash: ['M5 7h14', 'M9 7V4h6v3', 'M7 7l1 13h8l1-13', 'M10 11v6', 'M14 11v6'],
  check: ['M5 12.5 10 17.5 19 7'],
  left: ['M15 5l-7 7 7 7'],
  right: ['M9 5l7 7-7 7'],
  link: ['M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1', 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'],
  folder: ['M3 6h7l2 2h9v11H3z'],
  copy: ['M8 8h12v12H8z', 'M16 8V4H4v12h4'],
  eyeOff: ['M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6z', 'M4 4l16 16'],
  restore: ['M4 12a8 8 0 1 0 3-6.2', 'M4 4v5h5', 'M12 8v4l3 2'],
  merge: ['M6 4v6a6 6 0 0 0 6 6h6', 'M18 4v6', 'M15 13l3 3-3 3'],
  edit: ['M4 20h4L19 9l-4-4L4 16z', 'M13 7l4 4'],
  calendar: ['M4 6h16v14H4z', 'M4 10h16', 'M8 3v4', 'M16 3v4'],
  info: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M12 11v5', 'M12 8v.01'],
  warn: ['M12 4 3 20h18z', 'M12 10v4', 'M12 17v.01'],
  chevronUp: ['M6 15l6-6 6 6'],
  open: ['M14 4h6v6', 'M20 4 11 13', 'M18 14v6H4V6h6'],
  move: ['M12 3v18', 'M3 12h18', 'M9 6l3-3 3 3', 'M9 18l3 3 3-3', 'M6 9l-3 3 3 3', 'M18 9l3 3-3 3'],
  archive: ['M3 5h18v4H3z', 'M5 9v10h14V9', 'M10 13h4'],
  file: ['M6 3h8l4 4v14H6z', 'M14 3v4h4'],
  files: ['M8 3h7l4 4v11H8z', 'M15 3v4h4', 'M5 7v14h11'],
  quit: ['M10 4H5v16h5', 'M15 8l4 4-4 4', 'M19 12H9'],
};

export function icon(name, size = 16) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.classList.add('icon', `icon-${name}`);
  for (const d of PATHS[name] || []) {
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', d);
    svg.append(p);
  }
  return svg;
}
