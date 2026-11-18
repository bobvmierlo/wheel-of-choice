/* Wheel of Choice — small, state-free helpers.
 *
 * Pure functions only: they take their inputs as arguments and touch no
 * shared app state or the DOM, so they're easy to reason about (and to
 * reuse) on their own. See app.js for the stateful app logic.
 */

// Respect the OS "reduce motion" setting — the wheel spin and confetti
// check this to tone themselves down.
export const prefersReducedMotion = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// The page's language (set by i18n.js) decides how dates read.
const pageLang = () => document.documentElement.lang || undefined;

// "2026-07-20" -> "Mon, 20 Jul" (in the page's language).
export function prettyDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(pageLang(), { weekday: 'short', day: 'numeric', month: 'short' });
}

// An hour of the day (0–23) as friendly text: "midnight", "noon", "6pm" in
// English; other languages get their own clock style (e.g. "18:00").
export function fmtHour(h) {
  if ((pageLang() || 'en').startsWith('en')) {
    if (h === 0) return 'midnight';
    if (h === 12) return 'noon';
    return `${((h + 11) % 12) + 1}${h < 12 ? 'am' : 'pm'}`;
  }
  return new Date(2000, 0, 1, h).toLocaleTimeString(pageLang(), { hour: '2-digit', minute: '2-digit' });
}

// An ISO timestamp as a medium date + short time, or the raw string if
// it doesn't parse.
export function formatDateTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(pageLang(), { dateStyle: 'medium', timeStyle: 'short' });
}
