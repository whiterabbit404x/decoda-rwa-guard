import { PRICING_PLANS } from '../../pricing-plans';
import { HomeIcon } from './home-icons';
import { RevealGroup } from './scroll-reveal';
import site from './site.module.css';
import styles from './plans.module.css';

function isExternal(href: string): boolean {
  return /^(https?:|mailto:)/.test(href);
}

/**
 * The three Decoda RWA Guard plans, rendered from app/pricing-plans.ts — the
 * same data the homepage and /pricing read, aligned with the company website.
 * Every CTA is a plain link: /request-pilot may hand off to the company
 * website, and "Contact sales" is the company website's contact page.
 */
export function PlanCards({ headingLevel = 3 }: { headingLevel?: 2 | 3 }) {
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <RevealGroup className={`${styles.grid} ${site.staggerWide}`}>
      {PRICING_PLANS.map((plan) => (
        <article
          key={plan.key}
          className={styles.card}
          data-recommended={plan.featured ? 'true' : undefined}
          aria-labelledby={`plan-${plan.key}`}
        >
          {plan.badge ? <span className={styles.ribbon}>{plan.badge}</span> : null}
          <div className={styles.top}>
            <p className={styles.tagline}>{plan.tagline}</p>
            <Heading id={`plan-${plan.key}`} className={styles.name}>
              {plan.tier}
            </Heading>
            <p className={styles.price}>
              <span className={styles.amount}>{plan.price}</span>
              {plan.priceSub ? <span className={styles.suffix}>{plan.priceSub}</span> : null}
            </p>
            <p className={styles.priceNote}>{plan.priceNote}</p>
            <p className={styles.summary}>{plan.description}</p>
          </div>
          <ul className={styles.features}>
            {plan.highlights.map((feature) => (
              <li key={feature}>
                <span className={styles.check} aria-hidden="true">
                  <HomeIcon name="check" size={13} />
                </span>
                {feature}
              </li>
            ))}
          </ul>
          <a
            href={plan.ctaHref}
            className={`${plan.featured ? site.btnPrimary : site.btnSecondary} ${styles.cta}`}
          >
            {plan.ctaLabel}
            <HomeIcon name={isExternal(plan.ctaHref) ? 'arrowUpRight' : 'arrowRight'} size={16} />
          </a>
        </article>
      ))}
    </RevealGroup>
  );
}
