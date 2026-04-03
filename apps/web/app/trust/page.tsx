import Link from 'next/link';

export const dynamic = 'force-dynamic';

export default function TrustPage() {
  return (
    <main className="container authPage">
      <h1>Trust &amp; Reliability</h1>
      <p>Last updated: April 3, 2026.</p>
      <p>Decoda provides a production-polished pilot environment focused on continuity, clear failure behavior, and operator-grade evidence trails.</p>

      <h2>Operational expectations</h2>
      <ul>
        <li>Core authentication and workspace operations are continuously monitored.</li>
        <li>Live and degraded modes are surfaced intentionally to avoid hidden failures.</li>
        <li>Alerts, incidents, and export records preserve auditability for pilot teams.</li>
      </ul>

      <h2>Incident communication</h2>
      <p>If a customer-impacting issue occurs, Decoda communicates scope, mitigation, and follow-up actions through the configured workspace support channel and direct support responses.</p>

      <h2>Current commercial posture</h2>
      <p>Pilot deployments may run with <code>BILLING_PROVIDER=none</code>, which intentionally disables billing while preserving full operational workflows. See <Link href="/terms" prefetch={false}>Terms</Link> and <Link href="/support" prefetch={false}>Support</Link> for escalation paths.</p>
    </main>
  );
}
