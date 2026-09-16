import type { Metadata } from 'next';
import { headers } from 'next/headers';

import { PilotAuthProvider } from 'app/pilot-auth-context';
import { ThemeProvider } from 'app/theme-context';
import { THEME_INIT_SCRIPT } from 'app/theme-script';
import './styles.css';

export const metadata: Metadata = {
  title: 'Decoda RWA Guard',
  description: 'Customer-ready control center for tokenized treasury threat, compliance, and resilience operations',
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
    <html lang="en" suppressHydrationWarning>
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
