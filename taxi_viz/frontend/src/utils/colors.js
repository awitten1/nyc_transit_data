export const TAXI_RAMP = ['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026'];
export const BIKE_RAMP = ['#7bccc4', '#43a2ca', '#0868ac', '#084081', '#042040'];
export const OD_PALETTE = ['#e41a1c', '#ff7f00', '#4daf4a', '#984ea3', '#377eb8'];

/**
 * Interpolate a value into a color ramp.
 * @param {number} val
 * @param {number} min
 * @param {number} max
 * @param {string[]} ramp  Array of hex colors (2–N stops)
 * @returns {string}       Hex color
 */
export function scaleColor(val, min, max, ramp) {
  if (ramp.length === 0) return '#cccccc';
  if (max <= min) return ramp[ramp.length - 1];

  const t = Math.max(0, Math.min(1, (val - min) / (max - min)));
  const n = ramp.length - 1;
  const lo = Math.floor(t * n);
  const hi = Math.min(lo + 1, n);
  const frac = t * n - lo;

  return lerpHex(ramp[lo], ramp[hi], frac);
}

function hexToRgb(hex) {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
}

function lerpHex(a, b, t) {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  return rgbToHex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t);
}
