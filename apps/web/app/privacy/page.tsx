import Link from 'next/link';

export const dynamic = 'force-dynamic';

export default function PrivacyPage() {
  return (
    <main className="container authPage">
      <h1>Privacy Policy</h1>
      <p>Last updated: April 3, 2026.</p>
      <p>Decoda processes customer workspace data to provide monitoring, alerting, governance workflows, and export functionality.</p>

      <h2>Data we process</h2>
      <ul>
        <li>Account identity data (email, name, authentication metadata).</li>
        <li>Workspace configuration and membership records.</li>
        <li>Operational records such as alerts, incidents, findings, and exports.</li>
        <li>Security and audit logs needed for service integrity.</li>
      </ul>

      <h2>How data is used</h2>
      <p>Data is used to authenticate users, operate workspace features, preserve auditability, and support incident response. We do not sell customer data.</p>

      <h2>Retention and deletion</h2>
      <p>Workspace records remain available to support customer operations until deletion is requested or contractual retention ends. For export or deletion assistance, contact <a href="mailto:support@decoda.app">support@decoda.app</a>.</p>

      <h2>Security and subprocessors</h2>
      <p>Decoda relies on cloud infrastructure and operational subprocessors to host the service. We maintain least-privilege access, encrypted transport, and workspace-scoped controls as described on the <Link href="/security" prefetch={false}>Security page</Link>.</p>

      <h2>Questions</h2>
      <p>Use the <Link href="/support" prefetch={false}>Support page</Link> for privacy, export, account, or incident communication requests.</p>
    </main>
  );
}
