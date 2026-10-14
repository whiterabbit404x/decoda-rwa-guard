import type { Metadata, Viewport } from 'next';
import Link from 'next/link';

import { PublicShell } from '../components/home/public-shell';
import { LegalDocument } from '../components/home/site-blocks';
import { publicPageMetadata } from '../public-metadata';
export const dynamic = 'force-dynamic';

export const metadata: Metadata = publicPageMetadata({
  title: 'Support',
  description:
    'How to reach Decoda support for Decoda RWA Guard: onboarding, workspace operations, exports, account management and security reports.',
  path: '/support',
});

export const viewport: Viewport = { themeColor: '#ffffff', colorScheme: 'light' };

export default function SupportPage() {
  return (
    <PublicShell>
      <LegalDocument eyebrow="Support" title="Support" updated="April 3, 2026">
        <p>Need help with onboarding, workspace operations, exports, or account management? Contact Decoda support and include your workspace name plus impact summary.</p>

        <h2>Channels</h2>
        <ul>
          <li>General support: <a href="mailto:support@decodasecurity.com">support@decodasecurity.com</a></li>
          <li>Security reports: <a href="mailto:security@decodasecurity.com">security@decodasecurity.com</a></li>
          <li>Commercial / pilot expansion: <a href="mailto:sales@decodasecurity.com">sales@decodasecurity.com</a></li>
        </ul>

        <h2>What we can help with</h2>
        <ul>
          <li>Sign-in and workspace access recovery.</li>
          <li>Onboarding sequencing and integration setup checks.</li>
          <li>Data export assistance and account deletion requests.</li>
          <li>Operational incident and alert-routing troubleshooting.</li>
        </ul>

        <p>For platform reliability expectations, see <Link href="/trust" prefetch={false}>Trust &amp; Reliability</Link>.</p>
      </LegalDocument>
    </PublicShell>
  );
}
