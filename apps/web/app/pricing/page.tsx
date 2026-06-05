import Link from 'next/link';

export const dynamic = 'force-dynamic';

const tiers = [
  {
    tier: 'Pilot',
    price: 'Free',
    priceSub: 'no credit card required',
    description: 'Full product access for one workspace. Prove live monitoring before committing.',
    featured: false,
    ctaLabel: 'Start pilot →',
    ctaHref: '/sign-up',
    features: [
      { label: 'Workspaces', value: '1' },
      { label: 'Monitored contracts', value: '5' },
      { label: 'Networks', value: 'Ethereum mainnet' },
      { label: 'Live EVM telemetry', value: '✓' },
      { label: 'Threat & compliance detection', value: '✓' },
      { label: 'Alert routing', value: 'Email' },
      { label: 'Evidence packages', value: 'Up to 10' },
      { label: 'Audit log retention', value: '30 days' },
      { label: 'Webhook / Slack', value: '—' },
      { label: 'Incident playbooks', value: '—' },
      { label: 'Custom evidence templates', value: '—' },
      { label: 'Support', value: 'Community' },
    ],
  },
  {
    tier: 'Pro',
    price: '$299',
    priceSub: 'per month · 14-day trial',
    description: 'Scale across multiple protocols with priority alerts and unlimited evidence export.',
    featured: true,
    ctaLabel: 'Start Pro trial →',
    ctaHref: '/sign-up?plan=pro',
    features: [
      { label: 'Workspaces', value: '3' },
      { label: 'Monitored contracts', value: '50' },
      { label: 'Networks', value: 'Multi-chain (EVM)' },
      { label: 'Live EVM telemetry', value: '✓' },
      { label: 'Threat & compliance detection', value: '✓' },
      { label: 'Alert routing', value: 'Email + priority' },
      { label: 'Evidence packages', value: 'Unlimited' },
      { label: 'Audit log retention', value: '1 year' },
      { label: 'Webhook / Slack', value: '✓' },
      { label: 'Incident playbooks', value: '✓' },
      { label: 'Custom evidence templates', value: '—' },
      { label: 'Support', value: 'Priority email' },
    ],
  },
  {
    tier: 'Enterprise',
    price: 'Custom',
    priceSub: 'contact us for pricing',
    description: 'Dedicated deployment, compliance reporting, custom SLA, and dedicated support.',
    featured: false,
    ctaLabel: 'Contact sales →',
    ctaHref: 'mailto:sales@decoda.app',
    features: [
      { label: 'Workspaces', value: 'Unlimited' },
      { label: 'Monitored contracts', value: 'Unlimited' },
      { label: 'Networks', value: 'Multi-chain + private' },
      { label: 'Live EVM telemetry', value: '✓' },
      { label: 'Threat & compliance detection', value: '✓' },
      { label: 'Alert routing', value: 'Custom routing rules' },
      { label: 'Evidence packages', value: 'Unlimited' },
      { label: 'Audit log retention', value: 'Configurable' },
      { label: 'Webhook / Slack', value: '✓' },
      { label: 'Incident playbooks', value: '✓' },
      { label: 'Custom evidence templates', value: '✓' },
      { label: 'Support', value: 'Dedicated channel + SLA' },
    ],
  },
];

const faqs = [
  {
    q: 'Is the Pilot plan really free?',
    a: 'Yes. No credit card required. You get full product access — monitoring, detection, alerts, incidents, and evidence export — for one workspace and up to five contracts. The free tier is not time-limited.',
  },
  {
    q: 'How does the Pro trial work?',
    a: 'You get 14 days on the Pro tier at no charge. After the trial, you are billed monthly at $299. Cancel any time from your workspace settings before the trial ends to avoid a charge.',
  },
  {
    q: 'What counts as a "monitored contract"?',
    a: 'Each on-chain address you register as a monitoring target in your workspace counts as one monitored contract. EOA wallets and oracle feeds each count separately.',
  },
  {
    q: 'Can I upgrade or downgrade at any time?',
    a: 'Yes. Upgrades take effect immediately. Downgrades take effect at the end of the current billing period. Data and evidence packages are preserved when you downgrade.',
  },
  {
    q: 'How is billing handled?',
    a: 'Paid plans use Paddle for subscription management. Invoices are issued monthly. Payment methods: credit/debit card and wire transfer for Enterprise.',
  },
  {
    q: 'Is there a discount for annual billing?',
    a: 'Annual billing is available for Pro and Enterprise plans at a 15% discount. Contact sales@decoda.app to arrange.',
  },
];

function CheckIcon({ filled }: { filled?: boolean }) {
  if (filled) {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="7.5" fill="rgba(59,130,246,0.18)" stroke="rgba(59,130,246,0.35)" />
        <path d="M5 8l2 2 4-4" stroke="#60a5fa" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return <span className="pricingFeatureNa">—</span>;
}

function SmallShield() {
  return (
    <svg width="22" height="24" viewBox="0 0 26 28" fill="none" aria-hidden="true">
      <path d="M13 1.5L2 6.5V14c0 6.2 4.8 11.5 11 12.5 6.2-1 11-6.3 11-12.5V6.5L13 1.5z" fill="#3b82f6" />
      <path d="M9 14.5l2.5 2.5 5.5-5.5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function PricingPage() {
  return (
    <>
      {/* ── Sticky nav ─────────────────────────────────────── */}
      <a href="#pricing-main" className="skipToContent">Skip to main content</a>
      <header className="mktStandaloneNav" role="banner">
        <div className="mktStandaloneNavInner">
          <Link href="/" className="mktStandaloneNavLogo" prefetch={false}>
            <SmallShield />
            <span className="mktNavLogoText">
              <span className="mktStandaloneNavBrand">DECODA</span>
              <span className="mktStandaloneNavProduct">RWA GUARD</span>
            </span>
          </Link>
          <nav className="mktStandaloneNavLinks" aria-label="Site navigation">
            <Link href="/#platform" className="mktStandaloneNavLink" prefetch={false}>Product</Link>
            <Link href="/#how-it-works" className="mktStandaloneNavLink" prefetch={false}>How it works</Link>
            <Link href="/trust" className="mktStandaloneNavLink" prefetch={false}>Trust</Link>
            <Link href="/live-proof" className="mktStandaloneNavLink" prefetch={false}>Live Proof</Link>
          </nav>
          <div className="mktStandaloneNavRight">
            <Link href="/sign-in" className="mktStandaloneNavSignIn" prefetch={false}>Sign in</Link>
            <Link href="/sign-up" className="mktStandaloneNavCta" prefetch={false}>Start free →</Link>
          </div>
        </div>
      </header>

    <main id="pricing-main" className="pricingPage">

      <header className="pricingHero">
        <p className="mktSectionLabel">PRICING</p>
        <h1 className="pricingHeroTitle">Start free. Scale when you&rsquo;re ready.</h1>
        <p className="pricingHeroSubtitle">
          Full product access on every plan. No feature gating on core monitoring, detection, or evidence export.
          Billing via Paddle — cancel any time.
        </p>
      </header>

      {/* ── Pricing cards ──────────────────────────────────── */}
      <div className="pricingTierGrid">
        {tiers.map((tier) => (
          <article key={tier.tier} className={`pricingTierCard${tier.featured ? ' pricingTierCard--featured' : ''}`}>
            {tier.featured && <div className="pricingTierBadge">Most popular</div>}
            <div className="pricingTierName">{tier.tier}</div>
            <div className="pricingTierPrice">{tier.price}</div>
            <div className="pricingTierPriceSub">{tier.priceSub}</div>
            <p className="pricingTierDesc">{tier.description}</p>
            <Link
              href={tier.ctaHref}
              className={`pricingTierCta${tier.featured ? ' pricingTierCta--featured' : ''}`}
              prefetch={false}
            >
              {tier.ctaLabel}
            </Link>
          </article>
        ))}
      </div>

      {/* ── Feature comparison table ───────────────────────── */}
      <section className="pricingComparisonSection">
        <h2 className="pricingComparisonTitle">Full feature comparison</h2>
        <div className="pricingComparisonWrap">
          <table className="pricingComparisonTable">
            <thead>
              <tr>
                <th className="pricingComparisonFeatureCol">Feature</th>
                {tiers.map((t) => (
                  <th key={t.tier} className={t.featured ? 'pricingComparisonFeaturedCol' : ''}>{t.tier}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tiers[0].features.map((f, idx) => (
                <tr key={f.label}>
                  <td className="pricingComparisonFeatureLabel">{f.label}</td>
                  {tiers.map((tier) => (
                    <td key={tier.tier} className={`pricingComparisonValue${tier.featured ? ' pricingComparisonFeaturedValue' : ''}`}>
                      {tier.features[idx].value === '✓' ? (
                        <CheckIcon filled />
                      ) : tier.features[idx].value === '—' ? (
                        <span className="pricingFeatureNa">—</span>
                      ) : (
                        <span className="pricingFeatureText">{tier.features[idx].value}</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── FAQ ─────────────────────────────────────────────── */}
      <section className="pricingFaqSection">
        <h2 className="pricingFaqTitle">Pricing FAQ</h2>
        <div className="mktFaqGrid">
          {faqs.map((item) => (
            <div key={item.q} className="mktFaqItem">
              <p className="mktFaqQ">{item.q}</p>
              <p className="mktFaqA">{item.a}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Enterprise callout ──────────────────────────────── */}
      <section className="pricingEnterpriseCallout">
        <h2 className="pricingEnterpriseTitle">Need custom requirements?</h2>
        <p className="pricingEnterpriseDesc">
          Enterprise plans include dedicated deployment, custom evidence templates, compliance export formats,
          configurable audit log retention, and a dedicated support channel with SLA guarantees.
        </p>
        <a href="mailto:sales@decoda.app" className="mktCtaPrimary">
          Contact sales →
        </a>
      </section>

      <div className="trustFooterLinks">
        <Link href="/" prefetch={false} className="trustLink">← Home</Link>
        <Link href="/trust" prefetch={false} className="trustLink">Security &amp; Trust</Link>
        <Link href="/sign-up" prefetch={false} className="trustLink">Start free pilot</Link>
        <a href="mailto:support@decoda.app" className="trustLink">Support</a>
      </div>
    </main>
    </>
  );
}
