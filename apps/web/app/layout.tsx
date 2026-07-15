import type { Metadata } from 'next';
import { headers } from 'next/headers';

import { PilotAuthProvider } from 'app/pilot-auth-context';
import './styles.css';

export const metadata: Metadata = {
  title: 'Decoda RWA Guard',
  description: 'Customer-ready control center for tokenized treasury threat, compliance, and resilience operations',
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Reading request headers opts the App Router into request-time rendering so
  // Next.js can extract the per-request CSP nonce and apply it to its scripts.
  await headers();

  return (
    <html lang="en" data-theme="dark">
      <body>
        <PilotAuthProvider>{children}</PilotAuthProvider>
      </body>
    </html>
  );
}
