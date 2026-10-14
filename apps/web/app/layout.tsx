import type { Metadata } from 'next';
import localFont from 'next/font/local';
import { headers } from 'next/headers';

import { PilotAuthProvider } from 'app/pilot-auth-context';
import { GUARD_SITE_URL } from 'app/public-site-links';
import { ThemeProvider } from 'app/theme-context';
import { THEME_INIT_SCRIPT } from 'app/theme-script';
import './styles.css';

// Inter, self-hosted from the repository (SIL OFL 1.1, see fonts/Inter-LICENSE.txt).
// next/font/local needs no network at build time and serves the file from the
// app's own origin, so the production CSP (`font-src 'self'`) is unchanged. It
// only defines --font-inter; styles.css decides where the family is used.
const inter = localFont({
  src: './fonts/InterVariable-latin.woff2',
  weight: '100 900',
  style: 'normal',
  display: 'swap',
  variable: '--font-inter',
});

// The Guard-specific document title, and the canonical origin every relative
// metadata URL (canonical links, the share image) resolves against. Public
// pages set their own title, description and canonical path through
// app/public-metadata.ts. The tab icon comes from the file conventions in this
// directory — favicon.ico, icon.svg and apple-icon.png — all drawn from the
// company website's canonical Decoda shield (its app/icon.svg).
export const metadata: Metadata = {
  metadataBase: new URL(GUARD_SITE_URL),
  title: 'Decoda RWA Guard | Decoda Security',
  description:
    'Security monitoring, evidence-grounded investigation and policy-controlled response for tokenized financial infrastructure.',
  applicationName: 'Decoda RWA Guard',
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Reading request headers opts the App Router into request-time rendering so
  // Next.js can extract the per-request CSP nonce and apply it to its scripts.
  const requestHeaders = await headers();
  const nonce = requestHeaders.get('x-nonce') ?? undefined;

  return (
    // No `data-theme` is rendered here on purpose. The pre-paint script below
    // stamps the resolved theme onto <html> before the first frame, so there
    // is no flash in either direction — and because the server never emits the
    // attribute, there is nothing for React to reconcile against and no
    // hydration mismatch.
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <head>
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>
          <PilotAuthProvider>{children}</PilotAuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
