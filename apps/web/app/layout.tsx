import type { Metadata } from 'next';

import { PilotAuthProvider } from './pilot-auth-context';
import './styles.css';

export const metadata: Metadata = {
  title: 'Phase 1 Treasury Control Dashboard',
  description: 'Pilot-ready tokenized treasury risk control dashboard',
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
