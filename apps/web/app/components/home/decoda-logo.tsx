import styles from './site.module.css';

/**
 * The canonical Decoda shield — the same geometry, gradients and colours as
 * the company website (decoda-website components/company/logo.tsx and its
 * app/icon.svg, which this app now also serves as its favicon). Drawn inline so
 * it stays crisp at every size; decorative, because the wordmark beside it (or
 * the link's accessible name) carries the meaning.
 *
 * The gradients are defined ONCE per page by <DecodaShieldDefs /> (rendered by
 * the public shell) and referenced by id, so the mark stays a pure server
 * component with no per-instance ids and no inline styles.
 */

export const SHIELD_STROKE_ID = 'decoda-shield-stroke';
export const SHIELD_FILL_SOFT_ID = 'decoda-shield-fill-soft';
export const SHIELD_FILL_STRONG_ID = 'decoda-shield-fill-strong';

const SHIELD_PATH =
  'M20 3.2 33.4 8.1c.7.26 1.2.94 1.2 1.7v9.3c0 8.1-5.1 15.3-12.9 18.1l-1.1.4c-.4.14-.8.14-1.2 0l-1.1-.4C10.5 34.5 5.4 27.3 5.4 19.2V9.8c0-.76.5-1.44 1.2-1.7L20 3.2Z';
const CHECK_PATH = 'M14 20.4 18.2 24.6 26.6 15.8';

export function DecodaShieldDefs() {
  return (
    <svg className={styles.svgDefs} width="0" height="0" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={SHIELD_STROKE_ID} x1="8" y1="3" x2="32" y2="37" gradientUnits="userSpaceOnUse">
          <stop stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#0e7490" />
        </linearGradient>
        <linearGradient id={SHIELD_FILL_SOFT_ID} x1="20" y1="3" x2="20" y2="37" gradientUnits="userSpaceOnUse">
          <stop stopColor="rgba(20, 184, 166, 0.16)" />
          <stop offset="1" stopColor="rgba(11, 29, 56, 0.1)" />
        </linearGradient>
        <linearGradient id={SHIELD_FILL_STRONG_ID} x1="20" y1="3" x2="20" y2="37" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0f3b4f" />
          <stop offset="1" stopColor="#07142a" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/**
 * `soft` is the light-surface mark (header, footer); `strong` is the filled
 * mark for navy surfaces and large hero/CTA use — both exactly as the company
 * website draws them.
 */
export function DecodaShield({ className, strong = false }: { className?: string; strong?: boolean }) {
  return (
    <svg className={className} viewBox="0 0 40 40" fill="none" aria-hidden="true" focusable="false">
      <path
        d={SHIELD_PATH}
        fill={`url(#${strong ? SHIELD_FILL_STRONG_ID : SHIELD_FILL_SOFT_ID})`}
        stroke={`url(#${SHIELD_STROKE_ID})`}
        strokeWidth={strong ? 1.6 : 1.8}
      />
      <path
        d={CHECK_PATH}
        stroke={strong ? '#99f6e4' : `url(#${SHIELD_STROKE_ID})`}
        strokeWidth={2.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Full lockup: shield + "DECODA / RWA GUARD" wordmark — the company website's
 * "DECODA / SECURITY" lockup with the product named on the second line.
 */
export function DecodaLogo() {
  return (
    <span className={styles.logo}>
      <DecodaShield className={styles.logoMark} />
      <span className={styles.wordmark}>
        <span className={styles.wordmarkName}>DECODA</span>
        <span className={styles.wordmarkProduct}>RWA GUARD</span>
      </span>
    </span>
  );
}
