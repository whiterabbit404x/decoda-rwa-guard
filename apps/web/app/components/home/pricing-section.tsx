import { EXECUTION_NOTE } from '../../pricing-plans';
import { ROUTES, SECTION_IDS } from './home-data';
import { PlanCards } from './plan-cards';
import { Reveal } from './scroll-reveal';
import { SectionHeading, TextLink } from './site-ui';
import site from './site.module.css';
import styles from './home.module.css';

/**
 * Section H — pricing, from the canonical shared config (app/pricing-plans.ts),
 * which mirrors the company website's plans. The full comparison lives on
 * /pricing.
 */
export function PricingSection() {
  return (
    <section
      id={SECTION_IDS.pricing}
      className={`${site.section} ${site.sectionSoft}`}
      aria-labelledby="pricing-title"
    >
      <div className={site.container}>
        <SectionHeading
          id="pricing-title"
          align="center"
          eyebrow="Pricing"
          title="Start with a Scoped Pilot."
          lead="Evaluate Decoda RWA Guard against your own infrastructure, then move to recurring security operations when you are ready. Every plan is arranged with the Decoda team — there is no automatic checkout."
        />
        <PlanCards />
        <Reveal className={styles.pricingFoot}>
          <p>{EXECUTION_NOTE}</p>
          <TextLink href={ROUTES.pricing}>Compare plans in detail</TextLink>
        </Reveal>
      </div>
    </section>
  );
}
