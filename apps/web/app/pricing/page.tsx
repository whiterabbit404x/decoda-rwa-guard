import Link from 'next/link';

import { PRICING_PLANS } from '../pricing-plans';

export const dynamic = 'force-dynamic';

const faqs = [
  {
    q: 'What does the Pilot include?',
    a: 'The Pilot is a scoped, no-cost evaluation of the full product — monitoring, detection, alerts, incidents, and evidence export — for one workspace and up to five monitored contracts. It runs against your live RWA assets so you can validate Decoda before a production rollout.',
  },
  {
    q: 'Why does Scale start "from" $999?',
    a: 'Scale starts at $999 per month for the published limits — 3 workspaces and 25 monitored contracts. Deployments that need more workspaces, more monitored contracts, or additional networks are quoted on the Enterprise tier. Contact sales@decodasecurity.com to confirm your configuration.',
  },
  {
    q: 'What counts as a "monitored contract"?',
    a: 'Each on-chain address you register as a monitoring target in your workspace counts as one monitored contract. EOA wallets and oracle feeds each count separately.',
  },
  {
    q: 'Can I change plans at any time?',
    a: 'Yes. Upgrades take effect immediately. Downgrades take effect at the end of the current billing period. Data and evidence packages are preserved when you downgrade.',
  },
  {
    q: 'How is billing handled?',
    a: 'Paid plans use Paddle for subscription management. Invoices are issued monthly. Payment methods: credit/debit card and wire transfer for Enterprise.',
  },
  {
    q: 'Is there a discount for annual billing?',
    a: 'Annual billing is available for Scale and Enterprise at a 15% discount. Contact sales@decodasecurity.com to arrange.',
  },
];

function isExternal(href: string) {
  return href.startsWith('mailto:') || href.startsWith('http');
}

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
          </nav>
          <div className="mktStandaloneNavRight">
            <Link href="/sign-in" className="mktStandaloneNavSignIn" prefetch={false}>Sign in</Link>
            <Link href="/sign-up" className="mktStandaloneNavCta" prefetch={false}>Request Pilot →</Link>
          </div>
        </div>
      </header>

    <main id="pricing-main" className="pricingPage">

      <header className="pricingHero">
        <p className="mktSectionLabel">PRICING</p>
        <h1 className="pricingHeroTitle">Evaluate on live assets. Deploy in production.</h1>
        <p className="pricingHeroSubtitle">
          Full product access on every plan. No feature gating on core monitoring, detection, or evidence export.
          Paid plans are billed monthly via Paddle.
        </p>
      </header>

      {/* ── Pricing cards ──────────────────────────────────── */}
      <div className="pricingTierGrid">
        {PRICING_PLANS.map((plan) => (
          <article key={plan.key} className={`pricingTierCard${plan.featured ? ' pricingTierCard--featured' : ''}`}>
            {plan.badge && <div className="pricingTierBadge">{plan.badge}</div>}
            <div className="pricingTierName">{plan.tier}</div>
            <div className={`pricingTierPrice${plan.priceIsLabel ? ' pricingTierPrice--label' : ''}`}>{plan.price}</div>
            {/* Rendered even when empty so every card keeps the same vertical rhythm. */}
            <div className="pricingTierPriceSub">{plan.priceSub}</div>
            <p className="pricingTierDesc">{plan.description}</p>
            {isExternal(plan.ctaHref) ? (
              <a
                href={plan.ctaHref}
                className={`pricingTierCta${plan.featured ? ' pricingTierCta--featured' : ''}`}
              >
                {plan.ctaLabel}
              </a>
            ) : (
              <Link
                href={plan.ctaHref}
                className={`pricingTierCta${plan.featured ? ' pricingTierCta--featured' : ''}`}
                prefetch={false}
              >
                {plan.ctaLabel}
              </Link>
            )}
            <ul className="pricingTierFeatures">
              {plan.highlights.map((highlight) => (
                <li key={highlight} className="pricingTierFeature">
                  <CheckIcon filled />
                  <span>{highlight}</span>
                </li>
              ))}
            </ul>
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
                {PRICING_PLANS.map((plan) => (
                  <th key={plan.key} className={plan.featured ? 'pricingComparisonFeaturedCol' : ''}>{plan.tier}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PRICING_PLANS[0].comparison.map((row, idx) => (
                <tr key={row.label}>
                  <td className="pricingComparisonFeatureLabel">{row.label}</td>
                  {PRICING_PLANS.map((plan) => (
                    <td key={plan.key} className={`pricingComparisonValue${plan.featured ? ' pricingComparisonFeaturedValue' : ''}`}>
                      {plan.comparison[idx].value === '✓' ? (
                        <CheckIcon filled />
                      ) : plan.comparison[idx].value === '—' ? (
                        <span className="pricingFeatureNa">—</span>
                      ) : (
                        <span className="pricingFeatureText">{plan.comparison[idx].value}</span>
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
          Enterprise covers multi-network deployment, custom detection and policy controls, custom evidence
          templates, compliance and regulatory export formats, configurable audit-log retention, enterprise
          integrations, dedicated onboarding, and a custom SLA agreed with your team.
        </p>
        <a href="mailto:sales@decodasecurity.com" className="mktCtaPrimary">
          Contact Sales →
        </a>
      </section>

      <div className="trustFooterLinks">
        <Link href="/" prefetch={false} className="trustLink">← Home</Link>
        <Link href="/trust" prefetch={false} className="trustLink">Security &amp; Trust</Link>
        <Link href="/sign-up" prefetch={false} className="trustLink">Request a pilot</Link>
        <a href="mailto:support@decodasecurity.com" className="trustLink">Support</a>
      </div>
    </main>
    </>
  );
}
