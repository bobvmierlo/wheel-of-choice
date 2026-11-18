/* Wheel of Choice — translations.
 *
 * English is the source language: every text in the code is its own key,
 * with {named} placeholders. A language is one file, locales/<code>.json:
 *
 *   { "_language": "Nederlands", "messages": { "Log in": "Inloggen", … } }
 *
 * Drop a new file in locales/ and it appears in the language menu — see
 * wheelapp/i18n.py for the details and the checker. Anything a file
 * lacks shows in English.
 *
 *   t('Hi {name}', { name })            → translated, placeholders filled
 *   tn(n, '1 place', '{n} places')      → picks the form by n ({n} is filled in)
 *   N_('text')                          → marks a constant as translatable
 *                                         (translate it with t() where shown)
 *
 * The static page (index.html) is translated automatically on start-up:
 * text, and the placeholder/title/aria-label attributes. A paragraph with
 * inline markup (<strong>, <em>, <code>, <a>, <br>) is translated as one
 * piece, markup included. Switching language reloads the page.
 */
const SOURCE = 'en';
const STORE_KEY = 'wheel-lang';
const COOKIE = 'wheel_lang';
const INLINE = new Set(['STRONG', 'EM', 'B', 'I', 'CODE', 'A', 'BR', 'SMALL', 'KBD']);
const SKIP = new Set(['SCRIPT', 'STYLE', 'CANVAS', 'SVG', 'TEMPLATE']);
const ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];

let current = SOURCE;
let messages = {};

export const N_ = (text) => text;

export function t(text, vars) {
  let out = (current !== SOURCE && messages[text]) || text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return out;
}

export function tn(n, one, other, vars = {}) {
  return t(n === 1 ? one : other, { n, ...vars });
}

export function currentLanguage() {
  return current;
}

let languagesPromise = null;
export function availableLanguages() {
  languagesPromise ||= fetch('/api/i18n')
    .then((res) => res.json())
    .then((data) => data.languages)
    .catch(() => [{ code: SOURCE, name: 'English' }]);
  return languagesPromise;
}

function storedLanguage() {
  try { return localStorage.getItem(STORE_KEY) || ''; } catch { return ''; }
}

async function loadCatalog(code) {
  if (!/^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$/.test(code)) return null;
  try {
    const res = await fetch(`locales/${code}.json`, { cache: 'no-cache' });
    if (!res.ok) return null;
    return (await res.json()).messages || {};
  } catch {
    return null;
  }
}

// The saved choice, else the first of the browser's languages we have.
export async function initI18n() {
  const wanted = storedLanguage();
  const candidates = wanted ? [wanted] : (navigator.languages || [navigator.language || SOURCE])
    .flatMap((l) => [l, l.split('-')[0]]);
  const offered = new Set((await availableLanguages()).map((l) => l.code));
  for (const code of candidates) {
    if (code.split('-')[0] === SOURCE) break; // English is built in
    if (!offered.has(code)) continue;
    const catalog = await loadCatalog(code);
    if (catalog) {
      current = code;
      messages = catalog;
      break;
    }
  }
  document.documentElement.lang = current;
  // plain navigations (the sign-on bounce) tell the server via a cookie
  document.cookie = `${COOKIE}=${current}; path=/; max-age=31536000; samesite=lax`;
  if (current !== SOURCE) translateDom(document.documentElement);
}

export function setLanguage(code) {
  try { localStorage.setItem(STORE_KEY, code); } catch { /* falls back to the browser's language */ }
  document.cookie = `${COOKIE}=${code}; path=/; max-age=31536000; samesite=lax`;
  location.reload();
}

// ── Translating the static page ─────────────────────────────────────
export const normalize = (s) => s.replace(/\s+/g, ' ').trim();

function isUnit(el) {
  // a block of text with only inline markup inside: translate it whole
  if (!el.children.length) return false;
  for (const d of el.querySelectorAll('*')) if (!INLINE.has(d.tagName)) return false;
  return [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
}

function translateText(node) {
  const key = normalize(node.textContent);
  if (!key || !messages[key]) return;
  const lead = node.textContent.match(/^\s*/)[0];
  const trail = node.textContent.match(/\s*$/)[0];
  node.textContent = lead + messages[key] + trail;
}

export function translateDom(el) {
  if (SKIP.has(el.tagName) || el.closest('[translate="no"]')) return;
  for (const attr of ATTRS) {
    const v = el.getAttribute(attr);
    if (v && messages[normalize(v)]) el.setAttribute(attr, messages[normalize(v)]);
  }
  if (isUnit(el) && !INLINE.has(el.tagName)) {
    const key = normalize(el.innerHTML);
    if (messages[key]) el.innerHTML = messages[key];
    return;
  }
  for (const child of [...el.childNodes]) {
    if (child.nodeType === 3) translateText(child);
    else if (child.nodeType === 1) translateDom(child);
  }
}
