/*
 * Built-in destination catalogue.
 *
 * Every destination is tagged so the wheel can be filtered:
 *   budget   : 'low' | 'mid' | 'high'
 *   distance : 'regional' | 'europe' | 'longhaul'
 *   vibes    : any of 'nature' | 'culture' (museums & history) | 'food' | 'winter' (snow)
 *   seasons  : best time to go, any of 'spring' | 'summer' | 'autumn' | 'winter'
 *   party    : who it suits, any of 'couple' | 'group'
 *   favorite : starred by default — favourites get a double-width segment
 *              on the wheel (twice the chance to win)
 *
 * "regional" is meant as: reachable by car/train for a weekend-ish trip.
 * Tweak this list (or add your own destinations in the app) to taste.
 */
const BUILTIN_DESTINATIONS = [
  // ── Regional ───────────────────────────────────────────────────────────
  { id: 'be', name: 'Belgium',        flag: '🇧🇪', budget: 'low',  distance: 'regional', vibes: ['culture', 'food'],                       seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'de', name: 'Germany',        flag: '🇩🇪', budget: 'mid',  distance: 'regional', vibes: ['culture', 'nature', 'winter'],           seasons: ['spring', 'summer', 'autumn', 'winter'], party: ['couple', 'group'] },
  { id: 'fr', name: 'France',         flag: '🇫🇷', budget: 'mid',  distance: 'regional', vibes: ['culture', 'food', 'nature'],             seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'lu', name: 'Luxembourg',     flag: '🇱🇺', budget: 'mid',  distance: 'regional', vibes: ['nature', 'culture'],                     seasons: ['spring', 'summer', 'autumn'],           party: ['couple'] },
  { id: 'gb', name: 'United Kingdom', flag: '🇬🇧', budget: 'high', distance: 'regional', vibes: ['culture', 'nature', 'food'],             seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'], favorite: true },
  { id: 'dk', name: 'Denmark',        flag: '🇩🇰', budget: 'high', distance: 'regional', vibes: ['culture', 'food'],                       seasons: ['spring', 'summer'],                     party: ['couple', 'group'], favorite: true },

  // ── Europe ─────────────────────────────────────────────────────────────
  { id: 'ie', name: 'Ireland',        flag: '🇮🇪', budget: 'mid',  distance: 'europe',   vibes: ['nature', 'culture', 'food'],             seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'], favorite: true },
  { id: 'no', name: 'Norway',         flag: '🇳🇴', budget: 'high', distance: 'europe',   vibes: ['nature', 'winter'],                      seasons: ['summer', 'winter'],                     party: ['couple', 'group'], favorite: true },
  { id: 'se', name: 'Sweden',         flag: '🇸🇪', budget: 'high', distance: 'europe',   vibes: ['nature', 'culture', 'winter'],           seasons: ['spring', 'summer', 'winter'],           party: ['couple', 'group'], favorite: true },
  { id: 'fi', name: 'Finland',        flag: '🇫🇮', budget: 'high', distance: 'europe',   vibes: ['nature', 'winter'],                      seasons: ['summer', 'winter'],                     party: ['couple', 'group'], favorite: true },
  { id: 'is', name: 'Iceland',        flag: '🇮🇸', budget: 'high', distance: 'europe',   vibes: ['nature', 'winter'],                      seasons: ['summer', 'winter'],                     party: ['couple'] },
  { id: 'at', name: 'Austria',        flag: '🇦🇹', budget: 'mid',  distance: 'europe',   vibes: ['nature', 'culture', 'winter'],           seasons: ['summer', 'winter'],                     party: ['couple', 'group'] },
  { id: 'ch', name: 'Switzerland',    flag: '🇨🇭', budget: 'high', distance: 'europe',   vibes: ['nature', 'winter'],                      seasons: ['summer', 'winter'],                     party: ['couple'] },
  { id: 'it', name: 'Italy',          flag: '🇮🇹', budget: 'mid',  distance: 'europe',   vibes: ['culture', 'food', 'nature'],             seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'es', name: 'Spain',          flag: '🇪🇸', budget: 'mid',  distance: 'europe',   vibes: ['culture', 'food'],                       seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'pt', name: 'Portugal',       flag: '🇵🇹', budget: 'mid',  distance: 'europe',   vibes: ['culture', 'food', 'nature'],             seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'gr', name: 'Greece',         flag: '🇬🇷', budget: 'mid',  distance: 'europe',   vibes: ['culture', 'nature'],                     seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'si', name: 'Slovenia',       flag: '🇸🇮', budget: 'mid',  distance: 'europe',   vibes: ['nature', 'food'],                        seasons: ['spring', 'summer', 'autumn'],           party: ['couple'] },
  { id: 'cz', name: 'Czechia',        flag: '🇨🇿', budget: 'low',  distance: 'europe',   vibes: ['culture', 'food'],                       seasons: ['spring', 'summer', 'autumn', 'winter'], party: ['couple', 'group'] },
  { id: 'pl', name: 'Poland',         flag: '🇵🇱', budget: 'low',  distance: 'europe',   vibes: ['culture', 'nature'],                     seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'hu', name: 'Hungary',        flag: '🇭🇺', budget: 'low',  distance: 'europe',   vibes: ['culture', 'food'],                       seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'hr', name: 'Croatia',        flag: '🇭🇷', budget: 'mid',  distance: 'europe',   vibes: ['nature', 'culture'],                     seasons: ['summer', 'autumn'],                     party: ['couple', 'group'] },
  { id: 'tr', name: 'Türkiye',        flag: '🇹🇷', budget: 'low',  distance: 'europe',   vibes: ['culture', 'food'],                       seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },

  // ── Long-haul ──────────────────────────────────────────────────────────
  { id: 'ca', name: 'Canada',         flag: '🇨🇦', budget: 'high', distance: 'longhaul', vibes: ['nature', 'culture', 'winter'],           seasons: ['summer', 'autumn', 'winter'],           party: ['couple', 'group'], favorite: true },
  { id: 'za', name: 'South Africa',   flag: '🇿🇦', budget: 'mid',  distance: 'longhaul', vibes: ['nature', 'food', 'culture'],             seasons: ['autumn', 'winter', 'spring'],           party: ['couple', 'group'], favorite: true },
  { id: 'nz', name: 'New Zealand',    flag: '🇳🇿', budget: 'high', distance: 'longhaul', vibes: ['nature', 'culture'],                     seasons: ['winter', 'spring', 'autumn'],           party: ['couple'], favorite: true },
  { id: 'jp', name: 'Japan',          flag: '🇯🇵', budget: 'high', distance: 'longhaul', vibes: ['culture', 'food', 'nature', 'winter'],   seasons: ['spring', 'autumn', 'winter'],           party: ['couple', 'group'] },
  { id: 'us', name: 'United States',  flag: '🇺🇸', budget: 'high', distance: 'longhaul', vibes: ['culture', 'nature'],                     seasons: ['spring', 'summer', 'autumn'],           party: ['couple', 'group'] },
  { id: 'vn', name: 'Vietnam',        flag: '🇻🇳', budget: 'low',  distance: 'longhaul', vibes: ['food', 'nature', 'culture'],             seasons: ['winter', 'spring'],                     party: ['couple', 'group'] },
  { id: 'th', name: 'Thailand',       flag: '🇹🇭', budget: 'low',  distance: 'longhaul', vibes: ['food', 'nature', 'culture'],             seasons: ['winter', 'spring'],                     party: ['couple', 'group'] },
  { id: 'mx', name: 'Mexico',         flag: '🇲🇽', budget: 'mid',  distance: 'longhaul', vibes: ['food', 'culture', 'nature'],             seasons: ['winter', 'spring'],                     party: ['couple', 'group'] },
  { id: 'pe', name: 'Peru',           flag: '🇵🇪', budget: 'mid',  distance: 'longhaul', vibes: ['nature', 'culture', 'food'],             seasons: ['spring', 'summer'],                     party: ['couple', 'group'] },
  { id: 'cr', name: 'Costa Rica',     flag: '🇨🇷', budget: 'mid',  distance: 'longhaul', vibes: ['nature'],                                seasons: ['winter', 'spring'],                     party: ['couple'] },
  { id: 'au', name: 'Australia',      flag: '🇦🇺', budget: 'high', distance: 'longhaul', vibes: ['nature', 'culture', 'food'],             seasons: ['winter', 'spring', 'autumn'],           party: ['couple', 'group'] },
];
