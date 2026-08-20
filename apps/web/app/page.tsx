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
import styles from 'app/components/home/home.module.css';

export const dynamic = 'force-dynamic';

// Canonical plan data mirrors the standalone /pricing page (Pilot / Pro /
// Enterprise). Plan names, prices, the real 14-day Pro trial and the CTA
// destinations are reused verbatim — no billing logic is invented here. The
// CTA labels are intentionally kept in this file so the public-SaaS wording
// guardrail (tests/public-saas-hardening.spec.ts) keeps asserting on them.
const pricingTiers: PricingTier[] = [
  {
    tier: 'Pilot',
    price: 'Free',
    per: 'no credit card required',
    description: 'Full product access for one workspace. Prove live monitoring before committing.',
    featured: false,
    ctaLabel: 'Start pilot →',
    ctaHref: '/sign-up',
    features: [
      '1 workspace',
      '5 monitored contracts',
      'Live EVM telemetry',
      'Threat & compliance detection',
      'Evidence export (up to 10 packages)',
      'Community support',
    ],
  },
  {
    tier: 'Pro',
    price: '$299',
    per: '/ month · 14-day trial',
    description: 'Scale across multiple protocols with priority alerts and unlimited evidence export.',
    featured: true,
    badge: 'Most popular',
    ctaLabel: 'Start Pro trial →',
    ctaHref: '/sign-up?plan=pro',
    features: [
      '3 workspaces',
      '50 monitored contracts',
      'Multi-chain telemetry',
      'Priority alert routing',
      'Unlimited evidence packages',
      'Incident playbooks',
      'Priority email support',
    ],
  },
  {
    tier: 'Enterprise',
    price: 'Custom',
    per: 'contact us for pricing',
    description: 'Dedicated deployment, compliance reporting, custom SLA and a dedicated support channel.',
    featured: false,
    ctaLabel: 'Contact sales →',
    ctaHref: 'mailto:sales@decoda.app',
    features: [
      'Unlimited workspaces & assets',
      'Dedicated deployment',
      'Custom evidence templates',
      'Compliance & regulatory export',
      'Configurable audit log retention',
      'Dedicated support channel + SLA',
    ],
  },
];

const pricingNote =
  'Pro includes a 14-day trial. Billing is via Paddle — cancel any time from workspace settings. Enterprise pricing is custom.';

export default function MarketingHomePage() {
  const supportEmail = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? 'support@decoda.app';

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
        <PricingSection tiers={pricingTiers} note={pricingNote} />
        <FinalCTA />
      </main>

      <MarketingFooter supportEmail={supportEmail} />
    </div>
  );
}
