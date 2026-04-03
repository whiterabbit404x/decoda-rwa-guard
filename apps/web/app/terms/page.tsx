import Link from 'next/link';

export const dynamic = 'force-dynamic';

export default function TermsPage() {
  return (
    <main className="container authPage">
      <h1>Terms of Service</h1>
      <p>Last updated: April 3, 2026.</p>
      <p>Decoda RWA Guard is an operational monitoring and governance platform for tokenized treasury and real-world asset programs. These terms apply to every workspace, account, and API interaction.</p>

      <h2>Who this service is for</h2>
      <p>The service is designed for professional teams operating digital-asset and treasury workflows. You represent that your use is lawful in your jurisdiction and aligned with your organization&apos;s policy obligations.</p>

      <h2>Account and workspace responsibilities</h2>
      <ul>
        <li>Keep credentials and MFA factors secure.</li>
        <li>Maintain at least one workspace owner for administrative continuity.</li>
        <li>Provide accurate contact and workspace information.</li>
        <li>Review alerts, incidents, and workflow actions generated in your workspace.</li>
      </ul>

      <h2>Acceptable use</h2>
      <ul>
        <li>No abusive traffic, reverse engineering attempts, or disruption of service availability.</li>
        <li>No use of the platform to violate sanctions, securities, or data-protection obligations.</li>
        <li>No upload of malware or intentionally harmful payloads.</li>
      </ul>

      <h2>Service posture in this release phase</h2>
      <p>Decoda is currently offered in controlled pilot mode with billing intentionally disabled in environments configured with <code>BILLING_PROVIDER=none</code>. Access is still production-oriented and support-backed; commercial billing rollout is a separate later phase.</p>

      <h2>Data export and account assistance</h2>
      <p>You can request help with workspace export, account updates, or account closure by contacting <a href="mailto:support@decoda.app">support@decoda.app</a>. See also the <Link href="/privacy" prefetch={false}>Privacy Policy</Link> and <Link href="/support" prefetch={false}>Support</Link> page for response expectations.</p>
    </main>
  );
}
