import Link from 'next/link';

export const dynamic = 'force-dynamic';

export default function SupportPage() {
  return (
    <main className="container authPage">
      <h1>Support</h1>
      <p>Last updated: April 3, 2026.</p>
      <p>Need help with onboarding, workspace operations, exports, or account management? Contact Decoda support and include your workspace name plus impact summary.</p>

      <h2>Channels</h2>
      <ul>
        <li>General support: <a href="mailto:support@decoda.app">support@decoda.app</a></li>
        <li>Security reports: <a href="mailto:security@decoda.app">security@decoda.app</a></li>
        <li>Commercial / pilot expansion: <a href="mailto:sales@decoda.app">sales@decoda.app</a></li>
      </ul>

      <h2>What we can help with</h2>
      <ul>
        <li>Sign-in and workspace access recovery.</li>
        <li>Onboarding sequencing and integration setup checks.</li>
        <li>Data export assistance and account deletion requests.</li>
        <li>Operational incident and alert-routing troubleshooting.</li>
      </ul>

      <p>For platform reliability expectations, see <Link href="/trust" prefetch={false}>Trust &amp; Reliability</Link>.</p>
    </main>
  );
}
