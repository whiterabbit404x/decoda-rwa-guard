import Link from 'next/link';

export const dynamic = 'force-dynamic';

// The periods below are the ones services/api/app/pilot_retention.py seeds into
// workspace_retention_policies and that the retention worker applies. They are
// asserted against that module by
// apps/web/tests/pilot-retention-policy-source.spec.ts, so this page cannot state
// a period the product does not enforce.

export default function PrivacyPage() {
  return (
    <main className="container authPage">
      <h1>Privacy Policy</h1>
      <p>Last updated: September 18, 2026.</p>
      <p>Decoda processes customer workspace data to provide monitoring, alerting, governance workflows, and export functionality.</p>

      <h2>Data we process</h2>
      <ul>
        <li>Account identity data (email, name, authentication metadata).</li>
        <li>Workspace configuration and membership records.</li>
        <li>Operational records such as telemetry, detections, alerts, findings, incidents, and evidence exports.</li>
        <li>Security and audit logs needed for service integrity.</li>
      </ul>

      <h2>How data is used</h2>
      <p>Data is used to authenticate users, operate workspace features, preserve auditability, and support incident response. We do not sell customer data.</p>

      <h2>Retention while your Pilot is running</h2>
      <p>
        Each class of data has its own retention period, because raw chain telemetry, a security case file, and an
        accountability log are needed for different lengths of time. While your Pilot is active, records older than the
        period below are removed automatically by Decoda&rsquo;s retention worker:
      </p>
      <table>
        <thead>
          <tr><th>Data</th><th>Retained for</th><th>Then</th></tr>
        </thead>
        <tbody>
          <tr><td>Telemetry (raw monitored-chain observations)</td><td>90 days</td><td>Deleted</td></tr>
          <tr><td>Detections</td><td>180 days</td><td>Deleted</td></tr>
          <tr><td>Alerts and findings</td><td>180 days</td><td>Deleted</td></tr>
          <tr><td>Incidents, timelines, investigations and response history</td><td>365 days</td><td>Deleted</td></tr>
          <tr><td>Evidence exports (database record and stored file)</td><td>365 days</td><td>Deleted</td></tr>
          <tr><td>Audit and security logs</td><td>365 days</td><td>Anonymized, then deleted</td></tr>
          <tr><td>An individual user&rsquo;s identity data, on erasure request</td><td>30 days</td><td>Anonymized</td></tr>
        </tbody>
      </table>
      <p>
        Settings → Security shows the period in force for each class, where it came from, and whether it is being applied
        yet: a period that has been configured but has not started applying is labelled as such rather than shown as
        active, and a class with no policy reads &ldquo;no automatic deletion&rdquo; rather than a number nothing applies.
        A workspace owner can shorten or lengthen any of these periods through Decoda&rsquo;s API, or by asking us to; the
        change is audited and requires a recent re-authentication.
      </p>

      <h2>What happens when a Pilot ends</h2>
      <p>
        A Pilot evaluation is open-ended by default: it has no deadline and no deletion schedule, and the product does not
        show a countdown for one. A Pilot ends when Decoda ends it, or when a Pilot that was given an explicit end date
        reaches that date.
      </p>
      <ol>
        <li><strong>Grace period — 30 days.</strong> Your workspace stays readable and your evidence stays exportable for 30 days after the Pilot ends. The end date and the scheduled deletion date are both shown in the product.</li>
        <li><strong>After 30 days.</strong> Telemetry, detections, alerts, findings, incidents and evidence exports — including the stored export files — are permanently deleted, and audit logs are anonymized: the actor identity, IP address and event details are destroyed and only the action, the object, the timestamp and the integrity chain remain.</li>
        <li><strong>After 365 days from the end of the Pilot.</strong> The remaining anonymized audit record is deleted as well.</li>
      </ol>
      <p>
        If you upgrade, continue, or are reactivated at any point before the deletion runs, the scheduled deletion is
        cancelled and nothing is deleted.
      </p>

      <h2>What the schedule does not cover</h2>
      <p>
        The schedule above removes your operational security records. It does not remove your workspace configuration:
        the asset registry, monitoring targets and monitoring configuration, integrations and their stored credentials,
        API keys, webhooks, notification destinations, team membership and invitations, governance policies, and
        workspace settings are not on any automatic schedule. Removing them means deleting the workspace itself, which
        Decoda does on request — contact <a href="mailto:support@decodasecurity.com">support@decodasecurity.com</a>.
        Deleting your individual user account (Account settings) anonymizes your own identity and revokes your sessions;
        it does not delete the workspace. We list this rather than implying the schedule deletes everything, because it
        does not.
      </p>

      <h2>Requesting deletion earlier</h2>
      <p>
        A workspace owner or administrator can request immediate deletion at any time, through Decoda&rsquo;s API or by
        contacting <a href="mailto:support@decodasecurity.com">support@decodasecurity.com</a>. The request requires a recent
        re-authentication and an explicit typed confirmation, is recorded in the audit log, and produces a deletion
        receipt — a hash of the deletion report, listing what was removed and how many records, and containing none of the
        deleted content. Deleted records cannot be restored through any Decoda API.
      </p>

      <h2>Legal holds</h2>
      <p>
        A legal hold placed on a workspace overrides every deletion schedule on this page, including an immediate deletion
        request. Data under hold is retained until the hold is released; the product states when a hold is blocking a
        scheduled deletion. Releasing the hold allows the normal schedule to resume.
      </p>

      <h2>Backups</h2>
      <p>
        Deleted data is removed from Decoda&rsquo;s active systems on the schedule above. Residual encrypted copies may remain
        in our infrastructure providers&rsquo; backups until their normal backup-retention cycle completes, after which they are
        overwritten. We do not claim that deletion is instantaneous across backups, and we do not claim that no residual copies remain.
      </p>

      <h2>Security and subprocessors</h2>
      <p>Decoda relies on cloud infrastructure and operational subprocessors to host the service. We maintain least-privilege access, encrypted transport, and workspace-scoped controls as described on the <Link href="/security" prefetch={false}>Security page</Link>.</p>

      <h2>Questions</h2>
      <p>Use the <Link href="/support" prefetch={false}>Support page</Link> for privacy, export, account, or incident communication requests, or contact <a href="mailto:support@decodasecurity.com">support@decodasecurity.com</a>.</p>
    </main>
  );
}
