import { DEFAULT_THEME_PREFERENCE, THEME_STORAGE_KEY } from './theme-preference';

/**
 * The pre-paint theme script.
 *
 * Runs synchronously in <head>, BEFORE the browser paints the first frame, so
 * a dark-theme session never flashes a white page and a light-theme session
 * never flashes navy. It only writes attributes on <html> — the same
 * attributes the CSS token layer keys off — so there is nothing for React to
 * hydrate and no server/client mismatch: the server renders <html> with no
 * `data-theme`, this script stamps one, and React does not manage that
 * attribute.
 *
 * The production CSP is `script-src 'self' 'nonce-…'` with
 * `script-src-attr 'none'`, so this is injected as a nonced <script>, never
 * as an inline handler. The nonce comes from proxy.ts via the `x-nonce`
 * request header.
 *
 * Everything is wrapped in try/catch: a blocked `localStorage` (private mode,
 * blocked site data) must degrade to the default theme, never to a blank page.
 */
export const THEME_INIT_SCRIPT = `(function(){try{
var s=null;try{s=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)})}catch(e){}
var p=(s==='light'||s==='dark'||s==='system')?s:${JSON.stringify(DEFAULT_THEME_PREFERENCE)};
var d=p==='dark'||(p==='system'&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches);
var r=document.documentElement;
r.setAttribute('data-theme',d?'dark':'light');
r.setAttribute('data-theme-preference',p);
r.style.colorScheme=d?'dark':'light';
}catch(e){document.documentElement.setAttribute('data-theme','light')}})();`;
