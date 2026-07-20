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

// "2026-07-20" -> "Mon, 20 Jul" (in the viewer's locale).
export function prettyDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

// An hour of the day (0–23) as friendly text: "midnight", "noon", "6pm".
export function fmtHour(h) {
  if (h === 0) return 'midnight';
  if (h === 12) return 'noon';
  return `${((h + 11) % 12) + 1}${h < 12 ? 'am' : 'pm'}`;
}

// An ISO timestamp as a medium date + short time, or the raw string if
// it doesn't parse.
export function formatDateTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
