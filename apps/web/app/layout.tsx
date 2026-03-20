import type { Metadata } from 'next';

import { PilotAuthProvider } from './pilot-auth-context';
import './styles.css';

export const metadata: Metadata = {
  title: 'Decoda Pilot Control Center',
  description: 'Customer-ready pilot control center for treasury risk, compliance, and resilience',
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
