/* Wheel of Choice — shared constant tables.
 *
 * Pure data with no state and no DOM: the label lookups, the wheel-type
 * metadata, the segment palette and a couple of fixed keys. Split out of
 * app.js so the values live in one obvious place and can be imported
 * wherever they're needed. See app.js for the app logic itself.
 */

export const VIBE_LABELS = {
  nature: '🌲 nature', culture: '🏛️ culture & museums', food: '🍽️ food',
  beach: '🏖️ beach', nightlife: '🌃 nightlife', adventure: '🧗 adventure',
  wellness: '💆 wellness', winter: '⛷️ snow',
};

export const BUDGET_LABELS = { low: '💶 low budget', mid: '💶💶 mid budget', high: '💶💶💶 high budget' };

export const DISTANCE_LABELS = { regional: '🚗 regional', europe: '✈️ Europe', longhaul: '🌏 long-haul' };

// Filter groups that accept several values at once ('party' stays single-choice).
export const MULTI_FILTERS = ['budget', 'distance', 'vibe', 'season'];

export const WHEEL_TYPE_META = {
  holidays: { icon: '🌍', kicker: 'Your next holiday is…', noun: 'destination' },
  citytrips: { icon: '🏙️', kicker: 'Your next city trip is…', noun: 'destination' },
  restaurants: { icon: '🍽️', kicker: 'Tonight you\'re eating at…', noun: 'restaurant' },
};

// Stars are per person (starred_by). One star doubles a wheel segment;
// starred by two people triples it. Entries from before per-person stars
// only carry the shared `favorite` flag — worth one.
export const SEGMENT_COLORS = [
  '#ff5e7e', '#ffb84d', '#4dabff', '#6ee7a8',
  '#c084fc', '#f97362', '#38d0e0', '#facc15',
  '#fb7fb8', '#8aa9ff', '#5eddaf', '#ff9e6d',
];

// key predates the rename to Wheel of Choice — changing it would log
// every existing browser out for nothing
export const TOKEN_KEY = 'wheel-of-wander-token';
