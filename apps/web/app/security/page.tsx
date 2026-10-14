import type { Metadata, Viewport } from 'next';
import Link from 'next/link';

import { PublicShell } from '../components/home/public-shell';
import { LegalDocument } from '../components/home/site-blocks';
import { publicPageMetadata } from '../public-metadata';
export const dynamic = 'force-dynamic';

export const metadata: Metadata = publicPageMetadata({
  title: 'Security',
  description:
    'Core security controls of Decoda RWA Guard: workspace-scoped access control, MFA, audit logs, encryption of workspace secrets and how to report a security concern.',
  path: '/security',
});

export const viewport: Viewport = { themeColor: '#ffffff', colorScheme: 'light' };

export default function SecurityPage() {
  return (
    <PublicShell>
      <LegalDocument eyebrow="Security" title="Security" updated="April 3, 2026">
        <p>Decoda RWA Guard is built for operational trust: workspace-scoped access control, audit visibility, and resilient behavior across live and degraded dependencies.</p>

        <h2>Core controls</h2>
        <ul>
          <li>Role-based workspace access with owner/admin/analyst/viewer scopes.</li>
          <li>Session management, CSRF protection, and multi-factor authentication &mdash; mandatory for every human user of a Pilot workspace, configurable elsewhere.</li>
          <li>Audit logs for workspace administration and workflow-critical actions.</li>
          <li>HTTPS transport in deployed environments, terminated by the deployment platform; Decoda application code does not set the TLS version or cipher policy.</li>
          <li>Workspace secrets encrypted with AES-256-GCM under a managed, versioned application key; passwords hashed with salted scrypt.</li>
        </ul>

        <h2>Security reporting</h2>
        <p>To report a security concern, email <a href="mailto:security@decodasecurity.com">security@decodasecurity.com</a> with reproduction details and affected environment information. For non-security support, use <Link href="/support" prefetch={false}>Support</Link>.</p>

        <h2>Shared responsibility</h2>
        <p>Customers are responsible for user lifecycle management, workspace role assignment, and integration credential hygiene. Decoda is responsible for service operation, infrastructure hardening, and response communication.</p>
      </LegalDocument>
    </PublicShell>
  );
}
