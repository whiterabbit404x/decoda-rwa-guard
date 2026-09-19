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
        <li>Session management, CSRF protection, and multi-factor authentication &mdash; mandatory for every human user of a Pilot workspace, configurable elsewhere.</li>
        <li>Audit logs for workspace administration and workflow-critical actions.</li>
        <li>HTTPS transport in deployed environments, terminated by the deployment platform; Decoda application code does not set the TLS version or cipher policy.</li>
        <li>Workspace secrets encrypted with AES-256-GCM under a managed, versioned application key; passwords hashed with salted scrypt.</li>
      </ul>

      <h2>Security reporting</h2>
      <p>To report a security concern, email <a href="mailto:security@decodasecurity.com">security@decodasecurity.com</a> with reproduction details and affected environment information. For non-security support, use <Link href="/support" prefetch={false}>Support</Link>.</p>

      <h2>Shared responsibility</h2>
      <p>Customers are responsible for user lifecycle management, workspace role assignment, and integration credential hygiene. Decoda is responsible for service operation, infrastructure hardening, and response communication.</p>
    </main>
  );
}
