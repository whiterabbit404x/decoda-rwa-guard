import { cookies } from 'next/headers';
import type { ReactNode } from 'react';

import { SESSION_COOKIE_NAME, readLandingSessionHint } from '../../auth-guards';
import { DecodaShieldDefs } from './decoda-logo';
import type { NavKey } from './home-data';
import { MarketingFooter } from './marketing-footer';
import { MarketingHeader } from './marketing-header';
import styles from './site.module.css';

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
    <div className={styles.site}>
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
