import type { Metadata, Viewport } from 'next';

import { AiSection } from 'app/components/home/ai-section';
import { ControlsSection } from 'app/components/home/controls-section';
import { EcosystemSection } from 'app/components/home/ecosystem-section';
import { FinalCTA } from 'app/components/home/final-cta';
import { HeroSection } from 'app/components/home/hero-section';
import { HowItWorksSection } from 'app/components/home/how-it-works-section';
import { PlatformSection } from 'app/components/home/platform-section';
import { PricingSection } from 'app/components/home/pricing-section';
import { ProblemSection } from 'app/components/home/problem-section';
import { ProductSection } from 'app/components/home/product-section';
import { PublicShell } from 'app/components/home/public-shell';
import { publicPageMetadata } from 'app/public-metadata';

export const dynamic = 'force-dynamic';

const HOME_TITLE = 'Decoda RWA Guard | Decoda Security';

export const metadata: Metadata = publicPageMetadata({
  title: { absolute: HOME_TITLE },
  socialTitle: HOME_TITLE,
  description:
    'Decoda RWA Guard brings security monitoring, evidence-grounded investigation and policy-controlled response '
    + 'workflows together for tokenized financial infrastructure. Available for pilot evaluation.',
  path: '/',
});

export const viewport: Viewport = {
  themeColor: '#ffffff',
  colorScheme: 'light',
};

/**
 * The public homepage of Decoda RWA Guard — the same visual system as
 * www.decodasecurity.com, from header to footer:
 *
 *   Hero → A the problem → B the platform → C how Guard works →
 *   D product experience → E evidence-grounded AI → F institutional controls →
 *   G the Decoda ecosystem → H pricing → I final call to action
 *
 * Pricing renders from the canonical shared config in `app/pricing-plans.ts`
 * (PRICING_PLANS), which the standalone /pricing page uses as well. The page
 * never redirects an authenticated visitor away; the header offers them the
 * dashboard instead (see PublicShell).
 */
export default async function MarketingHomePage() {
  return (
    <PublicShell>
      <HeroSection />
      <ProblemSection />
      <PlatformSection />
      <HowItWorksSection />
      <ProductSection />
      <AiSection />
      <ControlsSection />
      <EcosystemSection />
      <PricingSection />
      <FinalCTA />
    </PublicShell>
  );
}
