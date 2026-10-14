import localFont from 'next/font/local';
import { cookies } from 'next/headers';
import type { ReactNode } from 'react';

import { SESSION_COOKIE_NAME, readLandingSessionHint } from '../../auth-guards';
import { DecodaShieldDefs } from './decoda-logo';
import type { NavKey } from './home-data';
import { MarketingFooter } from './marketing-footer';
import { MarketingHeader } from './marketing-header';
import styles from './site.module.css';

// JetBrains Mono, self-hosted from the repository (SIL OFL 1.1, see
// fonts/JetBrainsMono-LICENSE.txt): the company website's label and code face.
// Loaded here rather than in the root layout, so only the public pages fetch
// it; the authenticated product is unchanged. next/font/local serves it from
// this app's own origin, so the production CSP (`font-src 'self'`) holds.
const mono = localFont({
  src: [
    { path: '../../fonts/JetBrainsMono-latin-400.woff2', weight: '400', style: 'normal' },
    { path: '../../fonts/JetBrainsMono-latin-500.woff2', weight: '500', style: 'normal' },
  ],
  display: 'swap',
  preload: false,
  adjustFontFallback: false,
  fallback: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
  variable: '--font-mono-src',
});

/**
 * The frame every public page renders in: the light Decoda canvas, the skip
 * link, the shared shield gradients, the header and the footer.
 *
 * The header's auth-aware controls start from a server-side session HINT only
 * (see app/auth-guards.ts): whether a session cookie was present decides
 * whether they start neutral instead of flashing "Sign in" at a signed-in
 * visitor. It never renders an authenticated state on its own —
 * PilotAuthProvider confirms the session against the backend through
 * /api/auth/me — and an authenticated visitor is never redirected away.
 */
export async function PublicShell({ children, active }: { children: ReactNode; active?: NavKey }) {
  const cookieStore = await cookies();
  const sessionHint = readLandingSessionHint(cookieStore.get(SESSION_COOKIE_NAME)?.value);
  const supportEmail = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? 'support@decodasecurity.com';

  return (
    <div className={`${styles.site} ${mono.variable}`}>
      <a href="#main" className={styles.skipLink}>
        Skip to main content
      </a>
      <DecodaShieldDefs />
      <MarketingHeader sessionHint={sessionHint} active={active} />
      <main id="main" className={styles.main} tabIndex={-1}>
        {children}
      </main>
      <MarketingFooter supportEmail={supportEmail} />
    </div>
  );
}
