import type { Metadata } from 'next';

import { MarketingHeader } from 'app/components/home/marketing-header';
import { HeroSection } from 'app/components/home/hero-section';
import { WhatDecodaDoesSection } from 'app/components/home/what-decoda-does-section';
import { WhyThisMattersSection } from 'app/components/home/why-this-matters-section';
import { RWASecurityPlatformSection } from 'app/components/home/rwa-platform-section';
import { SecurityLifecycleSection } from 'app/components/home/security-lifecycle-section';
import { PlatformVisionSection } from 'app/components/home/platform-vision-section';
import { CompanyCTA } from 'app/components/home/company-cta';
import { MarketingFooter } from 'app/components/home/marketing-footer';
import styles from 'app/components/home/home.module.css';

export const dynamic = 'force-dynamic';

// Page-scoped metadata for the public company landing page. This overrides
// the app-wide default title from the root layout for this route only, so the
// authenticated product screens keep their own titles.
export const metadata: Metadata = {
  title: 'Decoda Security — Security infrastructure for blockchain financial systems',
  description:
    'Decoda Security helps institutions launch and scale digital asset and real-world asset programs with the controls, visibility, and resilience modern financial infrastructure demands. RWA Security is the flagship platform.',
};

export default function MarketingHomePage() {
  return (
    <div className={styles.page}>
      <a href="#main" className={styles.skipLink}>
        Skip to main content
      </a>

      <MarketingHeader />

      <main id="main">
        <HeroSection />
        <WhatDecodaDoesSection />
        <WhyThisMattersSection />
        <RWASecurityPlatformSection />
        <SecurityLifecycleSection />
        <PlatformVisionSection />
        <CompanyCTA />
      </main>

      <MarketingFooter />
    </div>
  );
}
