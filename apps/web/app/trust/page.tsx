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
    title: 'Evidence integrity',
    body: 'Evidence packages carry stable UUIDs assigned at generation time. Once exported, the record cannot be retroactively altered. Package IDs are logged in the immutable audit trail.',
  },
  {
    icon: 'auditlog',
    title: 'Immutable audit logs',
    body: 'Every governance action, incident record, response action, and export operation is written to an append-only audit log. Audit entries are not editable after creation.',
  },
  {
    icon: 'proofgates',
    title: 'Proof gates in CI',
    body: 'Every push triggers a release proof pipeline that validates the full evidence chain end-to-end: telemetry receipt → detection → alert → incident → evidence package. The pipeline is fail-closed — a broken gate blocks release.',
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
    q: 'Is Decoda SOC 2 certified?',
    a: 'Not yet. We are an early-access production SaaS. SOC 2 Type II audit is on our roadmap. We will not claim certification until it is achieved.',
  },
  {
    q: 'Is this GDPR-compliant?',
    a: 'The platform is designed to process operational data (on-chain addresses, telemetry events, governance records), not personal data. We minimise data collection. See our Privacy Policy for detail.',
  },
  {
    q: 'How do I report a security issue?',
    a: 'Email security@decoda.app with a description of the issue. We will acknowledge within 48 hours and coordinate disclosure. We do not have a formal bug bounty programme at this stage.',
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
      <path d="M13 1.5L2 6.5V14c0 6.2 4.8 11.5 11 12.5 6.2-1 11-6.3 11-12.5V6.5L13 1.5z" fill="#3b82f6" />
      <path d="M9 14.5l2.5 2.5 5.5-5.5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function TrustPage() {
  const securityEmail = 'security@decoda.app';
  const supportEmail = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? 'support@decoda.app';

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
            <Link href="/live-proof" className="mktStandaloneNavLink" prefetch={false}>Live Proof</Link>
          </nav>
          <div className="mktStandaloneNavRight">
            <Link href="/sign-in" className="mktStandaloneNavSignIn" prefetch={false}>Sign in</Link>
            <Link href="/sign-up" className="mktStandaloneNavCta" prefetch={false}>Start free →</Link>
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
          <span className="trustHeroBadge trustHeroBadge--green">Live EVM telemetry proven</span>
          <span className="trustHeroBadge trustHeroBadge--green">Evidence chain end-to-end verified</span>
          <span className="trustHeroBadge trustHeroBadge--green">100/100 production readiness</span>
          <span className="trustHeroBadge trustHeroBadge--yellow">SOC 2 — in roadmap, not yet certified</span>
        </div>
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
        <Link href="/live-proof" prefetch={false} className="trustLink">Live Proof</Link>
        <a href={`mailto:${supportEmail}`} className="trustLink">Support</a>
      </div>
    </main>
    </>
  );
}
