import Link from 'next/link';

import StatusBadge from './status-badge';

export const dynamic = 'force-dynamic';

const featureCards = [
  {
    title: 'Threat',
    description: 'Preemptive exploit detection, anomalous treasury-token market surveillance, and deterministic explainability for every alert.',
  },
  {
    title: 'Compliance',
    description: 'Transfer screening, governance controls, and policy-aware routing for tokenized treasury and real-world asset operations.',
  },
  {
    title: 'Resilience',
    description: 'Cross-ledger reconciliation, backstop decisions, and incident workflows that stay readable during degraded conditions.',
  },
];

export default async function MarketingHomePage() {
  return (
    <main className="container marketingPage">
      <section className="hero marketingHero">
        <div>
          <p className="eyebrow">Decoda RWA Guard</p>
          <h1>Risk control for tokenized treasuries and real-world assets.</h1>
          <p className="lede">Operate tokenized treasury programs with a customer-ready control layer for threat detection, compliance governance, and operational resilience—with dependable continuity and clear operator messaging when a dependency is temporarily unavailable.</p>
          <div className="heroActionRow">
            <Link href="/dashboard" className="primaryCta">Start free trial</Link>
            <a href={`mailto:${process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? "support@decoda.app"}`} className="secondaryCta">Contact sales</a>
            <Link href="/sign-in" className="tertiaryCta">Sign in</Link>
            <Link href="/sign-up" className="tertiaryCta">Sign up</Link>
          </div>
          <div className="chipRow">
            <span className="ruleChip">Railway API</span>
            <span className="ruleChip">Vercel web</span>
            <span className="ruleChip">Neon Postgres</span>
            <StatusBadge state="sample" compact />
          </div>
        </div>
        <div className="heroPanel marketingPanel">
          <p className="sectionEyebrow">Why customers buy</p>
          <h2>Make treasury-token operations feel governed, resilient, and board-ready.</h2>
          <p>Decoda RWA Guard gives issuers, operators, and compliance teams a single experience for monitoring exploit paths, approving governance controls, and proving operational discipline across live and degraded states.</p>
          <div className="summaryGrid compactSummaryGrid">
            <article className="metricCard"><p className="metricLabel">Threat coverage</p><p className="metricValue">24/7</p><p className="metricMeta">Contract, transaction, and market monitoring</p></article>
            <article className="metricCard"><p className="metricLabel">Governance trace</p><p className="metricValue">Saved</p><p className="metricMeta">Workspace-scoped records for workspace customers</p></article>
          </div>
        </div>
      </section>

      <section className="marketingSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Core platform</p>
            <h2>Threat, Compliance, and Resilience in one operator workflow</h2>
          </div>
        </div>
        <div className="threeColumnSection">
          {featureCards.map((card) => (
            <article key={card.title} className="dataCard polishedCard">
              <p className="sectionEyebrow">{card.title}</p>
              <h3>{card.title} controls</h3>
              <p>{card.description}</p>
              <Link href={`/${card.title.toLowerCase() === 'threat' ? 'dashboard' : card.title.toLowerCase()}`}>Explore {card.title.toLowerCase()}</Link>
            </article>
          ))}
        </div>
      </section>

      <section className="marketingSection customerTrustSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Customer trust</p>
            <h2>Production-ready controls</h2>
            <p className="lede">Live/degraded state handling, persisted workspace history, and deployment guidance for Railway, Vercel, and Neon make rollout predictable for security teams.</p>
          </div>
        </div>
        <div className="summaryGrid">
          <article className="metricCard"><p className="metricLabel">Deployment posture</p><p className="metricValue">Production-ready</p><p className="metricMeta">Documented env vars, migrations, and verification flow</p></article>
          <article className="metricCard"><p className="metricLabel">UX promise</p><p className="metricValue">Never blank</p><p className="metricMeta">Live, degraded, fallback, and sample states stay visible</p></article>
          <article className="metricCard"><p className="metricLabel">Workspace controls</p><p className="metricValue">Scoped</p><p className="metricMeta">User, workspace, and role-aware workspace operations</p></article>
        </div>
      </section>

      <section className="marketingSection pricingSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Commercial motion</p>
            <h2>Start self-serve, scale into enterprise operations</h2>
          </div>
        </div>
        <div className="threeColumnSection">
          <article className="dataCard polishedCard"><h3>Starter</h3><p className="metricValue">Start free trial</p><p>Self-serve onboarding, workspace setup, and integration delivery.</p></article>
          <article className="dataCard polishedCard"><h3>Operator</h3><p className="metricValue">Start free trial</p><p>Run authenticated workspaces, persist live records, and validate live vs fallback behavior.</p></article>
          <article className="dataCard polishedCard"><h3>Enterprise</h3><p className="metricValue">Custom</p><p>For issuers and infrastructure partners who need governance and resilience workflows at scale.</p></article>
        </div>
      </section>
    </main>
  );
}
