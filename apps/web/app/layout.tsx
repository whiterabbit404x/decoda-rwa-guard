import type { Metadata } from 'next';

import { PilotAuthProvider } from './pilot-auth-context';
import './styles.css';

export const metadata: Metadata = {
  title: 'Decoda RWA Guard',
  description: 'Customer-ready control center for tokenized treasury threat, compliance, and resilience operations',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <PilotAuthProvider>{children}</PilotAuthProvider>
      </body>
    </html>
  );
}
