import Link from 'next/link';

export const dynamic = 'force-dynamic';

// ─── Static data ─────────────────────────────────────────────

const liveControlItems = [
  {
    iconType: 'eth',
    title: 'Ethereum mainnet telemetry',
    subtitle: 'Blocks, mempool, logs, and risk signals',
    status: 'Live',
    statusClass: 'lcStatusLive',
  },
  {
    iconType: 'chain',
    title: 'Evidence chain',
    subtitle: 'Telemetry → Detection → Alert → Incident',
    status: 'Synced',
    statusClass: 'lcStatusLive',
  },
  {
    iconType: 'ops',
    title: 'Operator workflow',
    subtitle: 'Threats triaged, policies enforced',
    status: 'Operational',
    statusClass: 'lcStatusOp',
  },
  {
    iconType: 'deploy',
    title: 'Deployment posture',
    subtitle: 'Pilot-ready controls active',
    status: 'Healthy',
    statusClass: 'lcStatusHealth',
  },
];

const platformCards = [
  {
    iconType: 'threat',
    title: 'Threat Monitoring',
    description:
      'Preemptive exploit detection, anomalous treasury-token activity, and continuous surveillance across contracts and wallets.',
    link: '/dashboard',
    linkLabel: 'Explore threats',
  },
  {
    iconType: 'compliance',
    title: 'Compliance Governance',
    description:
      'Policy-aware screening, governance controls, and audit-ready reporting for treasury-token and real-world assets.',
    link: '/compliance',
    linkLabel: 'Explore compliance',
  },
  {
    iconType: 'resilience',
    title: 'Resilience Controls',
    description:
      'Cross-ledger reconciliation, backstop decisions, and incident playbooks that stay readable during degraded conditions.',
    link: '/resilience',
    linkLabel: 'Explore resilience',
  },
  {
    iconType: 'evidence',
    title: 'Evidence Export',
    description:
      'Exportable, cryptographically verifiable evidence packages for auditors, regulators, and internal stakeholders.',
    link: '/evidence',
    linkLabel: 'Explore evidence',
  },
];

const dashboardNav = [
  'Overview',
  'Assets',
  'Threats',
  'Compliance',
  'Resilience',
  'Evidence',
  'Reports',
  'Settings',
];

const dashMetrics = [
  { label: 'Monitored assets', value: '128', delta: '↑ 8 vs yesterday', deltaClass: 'mktDeltaUp', valClass: '' },
  { label: 'Open alerts', value: '7', delta: '↓ 2 vs yesterday', deltaClass: 'mktDeltaDown', valClass: 'mktValAlert' },
  { label: 'Incidents', value: '2', delta: 'No change', deltaClass: '', valClass: 'mktValWarn' },
  { label: 'Evidence packages', value: '46', delta: '↑ 6 vs yesterday', deltaClass: 'mktDeltaUp', valClass: '' },
];

const recentActivity = [
  {
    time: '12:41:07',
    event: 'Anomalous transfer pattern detected',
    asset: '0xA1f2…9c3B',
    severity: 'High',
    status: 'Investigating',
    sevClass: 'mktSevHigh',
    statClass: 'mktStatInv',
  },
  {
    time: '12:35:22',
    event: 'Policy screening – blocked',
    asset: 'USYC Treasury',
    severity: 'Medium',
    status: 'Blocked',
    sevClass: 'mktSevMed',
    statClass: 'mktStatBlock',
  },
  {
    time: '12:22:10',
    event: 'Oracle deviation above threshold',
    asset: 'NAV Oracle',
    severity: 'High',
    status: 'Alerted',
    sevClass: 'mktSevHigh',
    statClass: 'mktStatAlert',
  },
  {
    time: '12:10:55',
    event: 'Cross-ledger reconciliation mismatch',
    asset: 'rToken Vault',
    severity: 'Medium',
    status: 'Investigating',
    sevClass: 'mktSevMed',
    statClass: 'mktStatInv',
  },
];

// 6 rows × 12 cols of intensity values (0–1) for the threat heatmap
const heatmapRows: number[][] = [
  [0.1, 0.2, 0.4, 0.3, 0.8, 0.9, 0.7, 0.5, 0.3, 0.2, 0.1, 0.1],
  [0.1, 0.3, 0.5, 0.6, 0.9, 1.0, 0.8, 0.7, 0.4, 0.3, 0.1, 0.1],
  [0.2, 0.3, 0.4, 0.7, 0.8, 0.9, 0.7, 0.5, 0.4, 0.2, 0.1, 0.1],
  [0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.6, 0.4, 0.3, 0.1, 0.1, 0.1],
  [0.1, 0.1, 0.2, 0.3, 0.5, 0.6, 0.4, 0.3, 0.2, 0.1, 0.1, 0.1],
  [0.1, 0.1, 0.1, 0.2, 0.3, 0.4, 0.3, 0.2, 0.1, 0.1, 0.1, 0.1],
];

const whyCards = [
  {
    iconType: 'coverage',
    title: '24/7 Threat Coverage',
    description: 'Continuous monitoring across contracts, wallets, and oracles.',
  },
  {
    iconType: 'governance',
    title: 'Governance Saved',
    description: 'Workspace-scoped controls and immutable audit trails.',
  },
  {
    iconType: 'incident',
    title: 'Faster Incident Response',
    description: 'Playbooks and decision support when it matters most.',
  },
  {
    iconType: 'audit',
    title: 'Audit-Ready Evidence',
    description: 'Exportable, verifiable evidence trusted by auditors and regulators.',
  },
];

// ─── Icon components ──────────────────────────────────────────

function ShieldMark() {
  return (
    <svg width="26" height="28" viewBox="0 0 26 28" fill="none" aria-hidden="true">
      <path
        d="M13 1.5L2 6.5V14c0 6.2 4.8 11.5 11 12.5 6.2-1 11-6.3 11-12.5V6.5L13 1.5z"
        fill="#3b82f6"
      />
      <path
        d="M9 14.5l2.5 2.5 5.5-5.5"
        stroke="#fff"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function LcIcon({ type }: { type: string }) {
  if (type === 'eth') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M10 2L4 10l6 3.5L16 10 10 2z" fill="currentColor" opacity="0.85" />
        <path d="M4 10l6 3.5v4.5L4 10z" fill="currentColor" opacity="0.5" />
        <path d="M16 10l-6 3.5v4.5L16 10z" fill="currentColor" opacity="0.7" />
      </svg>
    );
  }
  if (type === 'chain') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle cx="6" cy="10" r="3.5" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="14" cy="10" r="3.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M9.5 10h1" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    );
  }
  if (type === 'ops') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle cx="10" cy="7" r="3" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="5" cy="15" r="2.5" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="15" cy="15" r="2.5" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2.5" y="2.5" width="15" height="15" rx="3" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6 10h8M10 6v8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function PlatformIcon({ type }: { type: string }) {
  if (type === 'threat') {
    return (
      <svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">
        <circle cx="13" cy="13" r="10" stroke="currentColor" strokeWidth="1.4" opacity="0.35" />
        <circle cx="13" cy="13" r="6" stroke="currentColor" strokeWidth="1.4" opacity="0.6" />
        <circle cx="13" cy="13" r="2.5" fill="currentColor" />
      </svg>
    );
  }
  if (type === 'compliance') {
    return (
      <svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">
        <path
          d="M13 2.5L4.5 7V14c0 5 3.75 9 8.5 10 4.75-1 8.5-5 8.5-10V7L13 2.5z"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path
          d="M9.5 13.5l2.5 2.5 4.5-4.5"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  if (type === 'resilience') {
    return (
      <svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">
        <circle cx="13" cy="13" r="10" stroke="currentColor" strokeWidth="1.4" />
        <path d="M13 3v20M3 13h20" stroke="currentColor" strokeWidth="1" opacity="0.35" />
        <circle cx="13" cy="13" r="3" fill="currentColor" opacity="0.7" />
      </svg>
    );
  }
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">
      <rect x="3.5" y="3.5" width="19" height="19" rx="3.5" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M8 10h10M8 14h10M8 18h6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function WhyIcon({ type }: { type: string }) {
  if (type === 'coverage') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <path
          d="M11 2L3 5.5V11c0 4.5 3.5 8 8 8.5 4.5-.5 8-4 8-8.5V5.5L11 2z"
          stroke="currentColor"
          strokeWidth="1.5"
          fill="none"
        />
      </svg>
    );
  }
  if (type === 'governance') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <circle cx="11" cy="8" r="3.5" stroke="currentColor" strokeWidth="1.5" />
        <path
          d="M4 19c0-3.866 3.134-7 7-7h1c3.866 0 7 3.134 7 7"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (type === 'incident') {
    return (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
        <path
          d="M11 3L4 11h7l-2 8 9-10h-7l2-6z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
      <circle cx="11" cy="11" r="9" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M7 11.5l2.5 2.5 5-5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function heatmapColor(val: number): string {
  if (val > 0.65) return `rgba(248,113,113,${(val * 0.9).toFixed(2)})`;
  if (val > 0.35) return `rgba(251,191,36,${(val * 0.9).toFixed(2)})`;
  return `rgba(74,222,128,${(val * 0.75 + 0.1).toFixed(2)})`;
}

// ─── Page ─────────────────────────────────────────────────────

export default async function MarketingHomePage() {
  const supportEmail = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? 'support@decoda.app';

  return (
    <>
      {/* ── Top nav ──────────────────────────────────────────── */}
      <header className="mktNav">
        <div className="mktNavInner">
          <Link href="/" className="mktNavLogo" prefetch={false}>
            <ShieldMark />
            <span className="mktNavLogoText">
              <span className="mktNavLogoBrand">DECODA</span>
              <span className="mktNavLogoProduct">RWA GUARD</span>
            </span>
          </Link>
          <nav className="mktNavLinks" aria-label="Main navigation">
            <a href="#platform" className="mktNavLink">Product</a>
            <a href="#platform" className="mktNavLink">Solutions</a>
            <Link href="/evidence" className="mktNavLink" prefetch={false}>Evidence</Link>
            <a href="#pricing" className="mktNavLink">Pricing</a>
            <Link href="/support" className="mktNavLink" prefetch={false}>Docs</Link>
          </nav>
          <div className="mktNavRight">
            <Link href="/sign-in" className="mktNavSignIn" prefetch={false}>Sign in</Link>
            <Link href="/sign-up" className="mktNavStartPilot" prefetch={false}>Start pilot →</Link>
          </div>
        </div>
      </header>

      <main className="mktPage">
        {/* ── Hero ─────────────────────────────────────────────── */}
        <section className="mktHero">
          <div className="mktHeroContent">
            <div className="mktPill">
              <span className="mktPillDot" aria-hidden="true" />
              Built for tokenized finance. Secured for the real world.
            </div>
            <h1 className="mktH1">
              Secure tokenized Treasuries
              <br />
              <span className="mktH1Accent">before exploits become systemic risk</span>
            </h1>
            <p className="mktSubtitle">
              Decoda RWA Guard continuously monitors RWA contracts, treasury-token operations,
              custody wallets, oracle/NAV integrity, and operator activity. Detect threats early,
              enforce compliance, respond with confidence, and export evidence your auditors will
              trust.
            </p>
            <div className="mktCtas">
              <Link href="/sign-up" className="mktCtaPrimary" prefetch={false}>
                Start pilot →
              </Link>
              <Link href="/evidence" className="mktCtaSecondary" prefetch={false}>
                View live evidence ↗
              </Link>
            </div>
            <div className="mktChips">
              <span className="mktChip mktChipLive">
                <span className="mktChipDot" aria-hidden="true" />
                Live EVM telemetry
              </span>
              <span className="mktChip">Treasury-token controls</span>
              <span className="mktChip">Evidence export</span>
              <span className="mktChip">Pilot-ready</span>
            </div>
          </div>

          {/* ── Live Control Layer ──────────────────────────────── */}
          <div className="mktHeroPanelWrap">
            <div className="mktHeroPanel">
              <div className="lcHeader">
                <span className="lcTitle">LIVE CONTROL LAYER</span>
                <span className="lcAllSystems">
                  <span className="lcDot" aria-hidden="true" />
                  All systems operational
                </span>
              </div>
              <div className="lcRows">
                {liveControlItems.map((item) => (
                  <div key={item.title} className="lcRow">
                    <div className={`lcRowIcon lcRowIcon--${item.iconType}`}>
                      <LcIcon type={item.iconType} />
                    </div>
                    <div className="lcRowBody">
                      <div className="lcRowTitle">{item.title}</div>
                      <div className="lcRowSub">{item.subtitle}</div>
                    </div>
                    <div className={`lcStatus ${item.statusClass}`}>{item.status}</div>
                  </div>
                ))}
              </div>
              <div className="lcFooter">
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 14 14"
                  fill="none"
                  aria-hidden="true"
                  className="lcFooterLockIcon"
                >
                  <rect x="1" y="5.5" width="12" height="7.5" rx="2" stroke="currentColor" strokeWidth="1.2" />
                  <path
                    d="M4 5.5V4a3 3 0 016 0v1.5"
                    stroke="currentColor"
                    strokeWidth="1.2"
                    strokeLinecap="round"
                  />
                </svg>
                <span className="lcFooterText">
                  Enterprise-grade security. Your data never leaves your boundary.
                </span>
                <Link href="/trust" className="lcFooterLink" prefetch={false}>
                  Learn more →
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* ── Core platform ─────────────────────────────────────── */}
        <section className="mktSection" id="platform">
          <div className="mktSectionHeader">
            <p className="mktSectionLabel">CORE PLATFORM</p>
            <h2 className="mktSectionTitle">
              End-to-end risk, compliance, and resilience for RWA operations.
            </h2>
          </div>
          <div className="mktPlatformGrid">
            {platformCards.map((card) => (
              <article key={card.title} className={`mktPlatformCard mktPlatformCard--${card.iconType}`}>
                <div className="mktPlatformCardIcon">
                  <PlatformIcon type={card.iconType} />
                </div>
                <h3 className="mktPlatformCardTitle">{card.title}</h3>
                <p className="mktPlatformCardDesc">{card.description}</p>
                <Link href={card.link} className="mktPlatformCardLink" prefetch={false}>
                  {card.linkLabel} →
                </Link>
              </article>
            ))}
          </div>
        </section>

        {/* ── Dashboard preview ─────────────────────────────────── */}
        <section className="mktSection" aria-label="Product dashboard preview">
          <div className="mktDashPreview" aria-hidden="true">
            {/* Sidebar */}
            <div className="mktDashSidebar">
              <div className="mktDashSidebarLogo">
                <ShieldMark />
                <span className="mktDashSidebarBrand">RWA GUARD</span>
              </div>
              <div className="mktDashNav">
                {dashboardNav.map((item, i) => (
                  <div
                    key={item}
                    className={`mktDashNavItem${i === 0 ? ' mktDashNavItemActive' : ''}`}
                  >
                    <span className="mktDashNavIcon">{item.charAt(0)}</span>
                    <span>{item}</span>
                  </div>
                ))}
              </div>
            </div>
            {/* Main area */}
            <div className="mktDashMain">
              <div className="mktDashHeader">
                <div className="mktDashHeaderLeft">
                  <h3 className="mktDashOverviewTitle">Overview</h3>
                  <span className="mktDashLive">
                    <span className="mktDashLiveDot" />
                    Live telemetry
                  </span>
                </div>
                <div className="mktDashHeaderRight">
                  <div className="mktDashFilter">All networks ▾</div>
                  <div className="mktDashFilter">Last 24h ▾</div>
                  <div className="mktDashFilter mktDashRefresh">↻</div>
                </div>
              </div>

              {/* Metric cards */}
              <div className="mktDashMetrics">
                {dashMetrics.map((m) => (
                  <div key={m.label} className="mktDashMetric">
                    <p className="mktDashMetricLabel">{m.label}</p>
                    <p className={`mktDashMetricVal${m.valClass ? ` ${m.valClass}` : ''}`}>
                      {m.value}
                    </p>
                    <p className={`mktDashMetricDelta${m.deltaClass ? ` ${m.deltaClass}` : ''}`}>
                      {m.delta}
                    </p>
                  </div>
                ))}
              </div>

              {/* Activity table + heatmap */}
              <div className="mktDashBody">
                <div className="mktDashActivity">
                  <div className="mktDashActivityHeader">
                    <span className="mktDashActivityTitle">Recent activity</span>
                    <span className="mktDashActivityViewAll">View all activity →</span>
                  </div>
                  <div className="mktDashTableWrap">
                    <table className="mktDashTable">
                      <thead>
                        <tr>
                          <th>TIME</th>
                          <th>EVENT</th>
                          <th>ASSET / CONTRACT</th>
                          <th>SEVERITY</th>
                          <th>STATUS</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recentActivity.map((row) => (
                          <tr key={`${row.time}-${row.asset}`}>
                            <td className="mktDashTime">{row.time}</td>
                            <td className="mktDashEvent">{row.event}</td>
                            <td className="mktDashAsset">{row.asset}</td>
                            <td>
                              <span className={`mktSev ${row.sevClass}`}>{row.severity}</span>
                            </td>
                            <td>
                              <span className={`mktStat ${row.statClass}`}>{row.status}</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="mktDashHeatmap">
                  <p className="mktDashHeatmapTitle">Threat heatmap</p>
                  <div className="mktHeatmapGrid">
                    {heatmapRows.map((row, ri) =>
                      row.map((val, ci) => (
                        <div
                          key={`${ri}-${ci}`}
                          className="mktHeatmapCell"
                          style={{ background: heatmapColor(val) }}
                        />
                      ))
                    )}
                  </div>
                  <div className="mktHeatmapLegend">
                    <span className="mktHeatmapLow">● Low</span>
                    <span className="mktHeatmapMed">● Medium</span>
                    <span className="mktHeatmapHigh">● High</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── Why customers buy ─────────────────────────────────── */}
        <section className="mktSection" id="why">
          <div className="mktSectionSplit">
            <div className="mktSectionHeader">
              <p className="mktSectionLabel">WHY CUSTOMERS BUY</p>
              <h2 className="mktSectionTitle">
                Built for governed, resilient,
                <br />
                and audit-ready operations.
              </h2>
            </div>
            <div className="mktWhyGrid">
              {whyCards.map((card) => (
                <article key={card.title} className="mktWhyCard">
                  <div className={`mktWhyIcon mktWhyIcon--${card.iconType}`}>
                    <WhyIcon type={card.iconType} />
                  </div>
                  <h3 className="mktWhyTitle">{card.title}</h3>
                  <p className="mktWhyDesc">{card.description}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* ── Final CTA ─────────────────────────────────────────── */}
        <section className="mktFinalCta" id="pricing">
          <h2 className="mktFinalCtaTitle">Start with a controlled pilot.</h2>
          <p className="mktFinalCtaText">
            Deploy Decoda RWA Guard for one treasury-token workspace, prove live telemetry, and
            export the evidence chain before expanding.
          </p>
          <div className="mktCtas mktFinalCtaActions">
            <Link href="/sign-up" className="mktCtaPrimary" prefetch={false}>
              Start pilot →
            </Link>
            <a href={`mailto:${supportEmail}`} className="mktCtaSecondary">
              Contact sales
            </a>
          </div>
        </section>
      </main>
    </>
  );
}
