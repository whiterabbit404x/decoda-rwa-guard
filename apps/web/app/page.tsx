import { MarketingHeader } from 'app/components/home/marketing-header';
import { HeroSection } from 'app/components/home/hero-section';
import { OperatingLayerSection } from 'app/components/home/operating-layer-section';
import { IncidentLifecycleSection } from 'app/components/home/incident-lifecycle-section';
import { ProductConsoleSection } from 'app/components/home/product-console-section';
import { EvidenceAISection } from 'app/components/home/evidence-ai-section';
import { RWASecuritySection } from 'app/components/home/rwa-security-section';
import { PolicyAutomationSection } from 'app/components/home/policy-automation-section';
import { TeamsSection } from 'app/components/home/teams-section';
import { PricingSection, type PricingTier } from 'app/components/home/pricing-section';
import { FinalCTA } from 'app/components/home/final-cta';
import { MarketingFooter } from 'app/components/home/marketing-footer';
import { PRICING_PLANS, PRICING_NOTE } from 'app/pricing-plans';
import styles from 'app/components/home/home.module.css';

export const dynamic = 'force-dynamic';

// Pricing renders from the canonical shared config in `app/pricing-plans.ts`,
// which the standalone /pricing page uses as well — plan names, prices, CTA
// labels and CTA routes cannot drift between the two surfaces. No billing
// logic is invented here; the CTA hrefs are real, existing routes.
const pricingTiers: PricingTier[] = PRICING_PLANS.map((plan) => ({
  tier: plan.tier,
  price: plan.price,
  priceIsLabel: plan.priceIsLabel,
  per: plan.priceSub,
  description: plan.description,
  featured: plan.featured,
  badge: plan.badge,
  ctaLabel: plan.ctaLabel,
  ctaHref: plan.ctaHref,
  features: plan.highlights,
}));

export default function MarketingHomePage() {
  const supportEmail = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? 'support@decodasecurity.com';

  return (
    <div className={styles.page}>
      <a href="#main" className={styles.skipLink}>
        Skip to main content
      </a>

      <MarketingHeader />

      <main id="main">
        <HeroSection />
        <OperatingLayerSection />
        <IncidentLifecycleSection />
        <ProductConsoleSection />
        <EvidenceAISection />
        <RWASecuritySection />
        <PolicyAutomationSection />
        <TeamsSection />
        <PricingSection tiers={pricingTiers} note={PRICING_NOTE} />
        <FinalCTA />
      </main>

      <MarketingFooter supportEmail={supportEmail} />
    </div>
  );
}
