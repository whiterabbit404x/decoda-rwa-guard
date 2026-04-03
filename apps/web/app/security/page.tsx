import Link from 'next/link';

export const dynamic = 'force-dynamic';

export default function SecurityPage() {
  return (
    <main className="container authPage">
      <h1>Security</h1>
      <p>Last updated: April 3, 2026.</p>
      <p>Decoda RWA Guard is built for operational trust: workspace-scoped access control, audit visibility, and resilient behavior across live and degraded dependencies.</p>

      <h2>Core controls</h2>
      <ul>
        <li>Role-based workspace access with owner/admin/analyst/viewer scopes.</li>
        <li>Session management, CSRF protection, and optional MFA workflows.</li>
        <li>Audit logs for workspace administration and workflow-critical actions.</li>
        <li>Encrypted transport in deployed environments and secret-based service integrations.</li>
      </ul>

      <h2>Security reporting</h2>
      <p>To report a security concern, email <a href="mailto:security@decoda.app">security@decoda.app</a> with reproduction details and affected environment information. For non-security support, use <Link href="/support" prefetch={false}>Support</Link>.</p>

      <h2>Shared responsibility</h2>
      <p>Customers are responsible for user lifecycle management, workspace role assignment, and integration credential hygiene. Decoda is responsible for service operation, infrastructure hardening, and response communication.</p>
    </main>
  );
}
