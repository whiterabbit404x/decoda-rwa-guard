import Link from 'next/link';

export const dynamic = 'force-dynamic';

const principles = [
  {
    icon: 'failclosed',
    title: 'Fail-closed by design',
    body: 'Status labels default to degraded or offline rather than healthy when data is missing or stale. No alert is never silently shown as healthy. No data is never shown as safe.',
  },
  {
    icon: 'truthful',
    title: 'No fake telemetry',
    body: 'Simulator and seeded data is never presented as live customer evidence. Runtime status is derived from canonical backend facts — heartbeat, poll, and telemetry are treated as distinct signals.',
  },
  {
    icon: 'isolation',
    title: 'Workspace isolation',
    body: 'All data is scoped to your workspace. Cross-tenant queries are not permitted. Customer data does not appear in another customer\'s workspace under any circumstances.',
  },
  {
    icon: 'evidence',
    title: 'Independently verifiable evidence',
    // Truthfulness: "tamper-evident", not "immutable". Nothing here physically
    // prevents a file being changed — what the package guarantees is that a
    // change is DETECTABLE by anyone, without our help. And "independently
    // verifiable" is stated only because a public-key signature makes it true:
    // it was not claimable while the only seal was a shared-secret HMAC.
    body: 'Evidence exports contain a SHA-256 hash for each file, a canonical manifest hash and a Merkle integrity root, '
      + 'so any change to a packaged artifact is detectable. New evidence packages are signed with Decoda\'s evidence-signing '
      + 'key and can be verified offline using the published public verification key. Verification does not require access to '
      + 'Decoda\'s application, API, database or any Decoda secret. Packages carry stable UUIDs and an audit-chain anchor.',
  },
  {
    icon: 'auditlog',
    title: 'Immutable audit logs',
    body: 'Every governance action, incident record, response action, and export operation is written to an append-only audit log. Audit entries are not editable after creation.',
  },
  {
    icon: 'proofgates',
    title: 'Fail-closed release proof gates',
    body: 'Release readiness is gated by a fail-closed proof pipeline that exercises the evidence chain — telemetry receipt → detection → alert → incident → evidence package. A broken gate blocks the release rather than shipping an unverified claim.',
  },
];

const dataProtectionItems = [
  'RPC provider credentials are stored encrypted and never logged in plaintext.',
  'Workspace secrets (webhook tokens, API keys) use envelope encryption.',
  'Evidence packages are stored with server-side encryption at rest.',
  'All API traffic uses TLS. Internal service communication is authenticated.',
  'Database connections use short-lived credentials with automatic rotation in production.',
  'No third-party analytics scripts run inside the authenticated product UI.',
];

const disclosureFaqs = [
  {
    q: 'What happens to our data when the Pilot ends?',
    a: 'Your workspace stays readable and your evidence stays exportable for 30 days after the Pilot ends. After that, '
      + 'telemetry, detections, alerts, findings, incidents and evidence exports \u2014 including the stored export files \u2014 '
      + 'are permanently deleted, and audit logs are anonymized. The remaining anonymized audit record is deleted 365 days '
      + 'after the Pilot ended. Upgrading or continuing before the deletion runs cancels it. An active Pilot has no deletion '
      + 'schedule at all. Exact per-class periods are in the Privacy Policy.',
  },
  {
    q: 'Can we have our data deleted sooner?',
    a: 'Yes. A workspace owner or administrator can request immediate deletion from Settings \u2192 Security. It requires a '
      + 'recent re-authentication and a typed confirmation, is recorded in the audit log, and returns a deletion receipt \u2014 '
      + 'a hash of the report listing what was removed, containing none of the deleted content. A legal hold overrides it.',
  },
  {
    q: 'Is deleted data really gone, including backups?',
    a: 'Deleted data is removed from our active systems on the stated schedule and cannot be restored through any Decoda API. '
      + 'Residual encrypted copies may remain in our infrastructure providers\u2019 backups until their normal backup-retention '
      + 'cycle completes, after which they are overwritten. We do not claim zero residual data, because we cannot prove it.',
  },
  {
    q: 'Can Decoda staff see our data?',
    a: 'Not your monitored data. Decoda personnel cannot read your telemetry, detections, alerts, incidents or '
      + 'evidence without ordinary membership of your workspace, and there is no impersonation or log-in-as '
      + 'mechanism \u2014 no Decoda tool can sign in as one of your users. Authorized Decoda personnel can see '
      + 'account-level information about your organization \u2014 plan, status, usage, workspace and member '
      + 'metadata, and the evaluation feedback you submitted \u2014 through an internal console. Customer-specific '
      + 'access there is recorded with the staff actor, the organization and workspace context, the operation and '
      + 'the timestamp, and those events appear in your own workspace audit history as \u201cDecoda staff\u201d, '
      + 'marked read-only or change. Cross-tenant listings \u2014 an internal directory spanning all customers '
      + '\u2014 are recorded internally and are not shown in any one customer\u2019s history.',
  },
  {
    q: 'Is Decoda SOC 2 certified?',
    a: 'Not yet. We are an early-access production SaaS. SOC 2 Type II audit is on our roadmap. We will not claim certification until it is achieved.',
  },
  {
    q: 'Is this GDPR-compliant?',
    a: 'The platform is designed to process operational data (on-chain addresses, telemetry events, governance records), not personal data. We minimise data collection. See our Privacy Policy for detail.',
  },
  {
    q: 'How do I report a security issue?',
    a: 'Email security@decodasecurity.com with a description of the issue. We will acknowledge within 48 hours and coordinate disclosure. We do not have a formal bug bounty programme at this stage.',
  },
  {
    q: 'Where is data stored?',
    a: 'Production data is stored in a managed PostgreSQL database hosted in the EU (Neon). Evidence exports can be configured to land in your own S3-compatible bucket.',
  },
];

function PrincipleIcon({ type }: { type: string }) {
  if (type === 'failclosed') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <path d="M11 2L3 5.5V11c0 4.5 3.5 8 8 8.5 4.5-.5 8-4 8-8.5V5.5L11 2z" stroke="currentColor" strokeWidth="1.5" fill="none" />
        <path d="M8 11l2 2 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (type === 'truthful') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <circle cx="11" cy="11" r="9" stroke="currentColor" strokeWidth="1.5" />
        <path d="M11 6v5l3 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  }
  if (type === 'isolation') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <rect x="2" y="2" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <rect x="12" y="2" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <rect x="2" y="12" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <rect x="12" y="12" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    );
  }
  if (type === 'evidence') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <rect x="4" y="2" width="14" height="18" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <path d="M7 7h8M7 11h8M7 15h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    );
  }
  if (type === 'auditlog') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <path d="M5 4h12a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V5a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.5" />
        <path d="M8 9h6M8 13h6M8 17h3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        <path d="M8 5V3M14 5V3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
      <path d="M11 2l2 4h5l-4 3 2 5-5-3-5 3 2-5-4-3h5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

function TrustShield() {
  return (
    <svg width="22" height="24" viewBox="0 0 26 28" fill="none" aria-hidden="true">
      <path d="M13 1.5L2 6.5V14c0 6.2 4.8 11.5 11 12.5 6.2-1 11-6.3 11-12.5V6.5L13 1.5z" fill="var(--accent-blue)" />
      <path d="M9 14.5l2.5 2.5 5.5-5.5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function TrustPage() {
  const securityEmail = 'security@decodasecurity.com';
  const supportEmail = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? 'support@decodasecurity.com';

  return (
    <>
      {/* ── Skip to main content ─────────────────────────────── */}
      <a href="#trust-main" className="skipToContent">Skip to main content</a>

      {/* ── Sticky nav ───────────────────────────────────────── */}
      <header className="mktStandaloneNav" role="banner">
        <div className="mktStandaloneNavInner">
          <Link href="/" className="mktStandaloneNavLogo" prefetch={false}>
            <TrustShield />
            <span className="mktNavLogoText">
              <span className="mktStandaloneNavBrand">DECODA</span>
              <span className="mktStandaloneNavProduct">RWA GUARD</span>
            </span>
          </Link>
          <nav className="mktStandaloneNavLinks" aria-label="Site navigation">
            <Link href="/#platform" className="mktStandaloneNavLink" prefetch={false}>Product</Link>
            <Link href="/pricing" className="mktStandaloneNavLink" prefetch={false}>Pricing</Link>
          </nav>
          <div className="mktStandaloneNavRight">
            <Link href="/sign-in" className="mktStandaloneNavSignIn" prefetch={false}>Sign in</Link>
            <Link href="/request-pilot" className="mktStandaloneNavCta" prefetch={false}>Request Pilot →</Link>
          </div>
        </div>
      </header>

    <main id="trust-main" className="trustPage">

      {/* ── Hero ─────────────────────────────────────────────── */}
      <header className="trustHero">
        <p className="mktSectionLabel">SECURITY &amp; TRUST</p>
        <h1 className="trustHeroTitle">
          Designed to be truthful, fail-closed,<br />and auditable by default.
        </h1>
        <p className="trustHeroSubtitle">
          Decoda RWA Guard is an early-access production SaaS. We make honest claims about what we are and what we are not.
          This page documents the security and trust posture of the platform as it stands today.
        </p>
        <div className="trustHeroBadges">
          <span className="trustHeroBadge trustHeroBadge--green">Fail-closed by design</span>
          <span className="trustHeroBadge trustHeroBadge--green">Workspace-isolated</span>
          <span className="trustHeroBadge trustHeroBadge--green">Immutable audit trail</span>
          <span className="trustHeroBadge trustHeroBadge--yellow">SOC 2 — in roadmap, not yet certified</span>
        </div>
        <p className="trustHeroProofLine">
          Runtime, telemetry and evidence-chain status is derived from canonical backend facts and surfaced
          fail-closed inside the product — degraded or missing signals are shown as such, never as healthy.
        </p>
      </header>

      {/* ── Principles grid ──────────────────────────────────── */}
      <section className="trustSection">
        <h2 className="trustSectionTitle">Secure-by-design principles</h2>
        <div className="trustPrincipleGrid">
          {principles.map((p) => (
            <article key={p.title} className="trustPrincipleCard">
              <div className={`trustPrincipleIcon trustPrincipleIcon--${p.icon}`}>
                <PrincipleIcon type={p.icon} />
              </div>
              <h3 className="trustPrincipleTitle">{p.title}</h3>
              <p className="trustPrincipleBody">{p.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* ── Truthfulness rules callout ────────────────────────── */}
      <section className="trustSection trustCalloutSection">
        <div className="trustCallout">
          <h2 className="trustCalloutTitle">Truthfulness rules — enforced in code</h2>
          <p className="trustCalloutIntro">
            These rules are implemented in the product codebase, not just written in a policy document.
            They govern every status label, every runtime summary, and every evidence export.
          </p>
          <ul className="trustRuleList">
            <li>No data is never shown as safe.</li>
            <li>No alert is never silently shown as healthy.</li>
            <li>Simulator or seeded data is never presented as customer evidence.</li>
            <li>Runtime status is derived from canonical backend facts — not frontend assumptions.</li>
            <li>Heartbeat, poll, and telemetry are distinct signals. Heartbeat alone does not claim live monitoring.</li>
            <li>Telemetry is not &ldquo;current&rdquo; when it is missing or stale. The UI surfaces this explicitly.</li>
            <li>Live monitoring is not claimed as healthy when reporting systems are at zero.</li>
          </ul>
        </div>
      </section>

      {/* ── Data protection ──────────────────────────────────── */}
      <section className="trustSection">
        <h2 className="trustSectionTitle">Data protection overview</h2>
        <p className="trustSectionIntro">
          The following describes our current data handling posture. This is not a comprehensive security policy —
          it covers the most important operational facts for pilot and early paid customers.
        </p>
        <ul className="trustDataList">
          {dataProtectionItems.map((item) => (
            <li key={item} className="trustDataItem">
              <span className="trustDataCheck" aria-hidden="true">✓</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* ── Disclosure FAQ ───────────────────────────────────── */}
      <section className="trustSection">
        <h2 className="trustSectionTitle">Certifications &amp; disclosure</h2>
        <div className="trustFaqGrid">
          {disclosureFaqs.map((item) => (
            <div key={item.q} className="trustFaqItem">
              <p className="trustFaqQ">{item.q}</p>
              <p className="trustFaqA">{item.a}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Responsible disclosure ───────────────────────────── */}
      <section className="trustSection trustDisclosureSection">
        <div className="trustDisclosureCard">
          <h2 className="trustDisclosureTitle">Responsible disclosure</h2>
          <p className="trustDisclosureBody">
            If you believe you have found a security vulnerability in Decoda RWA Guard, please report it
            responsibly. We do not have a formal bug bounty programme at this stage, but we take every
            report seriously and will coordinate disclosure with you.
          </p>
          <p className="trustDisclosureContact">
            Email:{' '}
            <a href={`mailto:${securityEmail}`} className="trustLink">
              {securityEmail}
            </a>
            {' '}· Response within 48 hours.
          </p>
        </div>
      </section>

      {/* ── Operational expectations ─────────────────────────── */}
      <section className="trustSection">
        <h2 className="trustSectionTitle">Operational expectations</h2>
        <ul className="trustOpsList">
          <li>Core authentication and workspace operations are continuously monitored.</li>
          <li>Live and degraded modes are surfaced intentionally to avoid hidden failures.</li>
          <li>Alerts, incidents, and export records preserve auditability for pilot teams.</li>
          <li>If a customer-impacting issue occurs, Decoda communicates scope, mitigation, and follow-up through the configured workspace support channel.</li>
          <li>Pilot deployments may run with billing disabled. Full operational workflows are preserved regardless of billing state.</li>
        </ul>
      </section>

      {/* ── Footer links ──────────────────────────────────────── */}
      <div className="trustFooterLinks">
        <Link href="/" prefetch={false} className="trustLink">← Home</Link>
        <Link href="/pricing" prefetch={false} className="trustLink">Pricing</Link>
        <Link href="/privacy" prefetch={false} className="trustLink">Privacy Policy</Link>
        <Link href="/terms" prefetch={false} className="trustLink">Terms of Service</Link>
        <a href={`mailto:${supportEmail}`} className="trustLink">Support</a>
      </div>
    </main>
    </>
  );
}
