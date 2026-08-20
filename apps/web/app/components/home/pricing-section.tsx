import Link from 'next/link';

import { HomeIcon } from './home-icons';
import styles from './home.module.css';

export interface PricingTier {
  tier: string;
  price: string;
  per: string;
  description: string;
  featured: boolean;
  badge?: string;
  ctaLabel: string;
  ctaHref: string;
  features: string[];
}

function isExternal(href: string) {
  return href.startsWith('mailto:') || href.startsWith('http');
}

export function PricingSection({ tiers, note }: { tiers: PricingTier[]; note: string }) {
  return (
    <section className={styles.section} id="pricing">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>Pricing</p>
          <h2 className={styles.sectionTitle}>Simple pricing. Enterprise ready.</h2>
          <p className={styles.sectionLead}>
            Full product access on every plan. No feature gating on core monitoring, detection or
            evidence export.
          </p>
        </div>

        <div className={styles.priceGrid}>
          {tiers.map((tier) => (
            <article
              key={tier.tier}
              className={`${styles.priceCard}${tier.featured ? ` ${styles.priceFeatured}` : ''}`}
            >
              {tier.badge && <span className={styles.priceBadge}>{tier.badge}</span>}
              <div className={styles.priceTier}>{tier.tier}</div>
              <div className={styles.priceAmountRow}>
                <span className={styles.priceAmount}>{tier.price}</span>
                <span className={styles.pricePer}>{tier.per}</span>
              </div>
              <p className={styles.priceDesc}>{tier.description}</p>
              {isExternal(tier.ctaHref) ? (
                <a
                  href={tier.ctaHref}
                  className={`${tier.featured ? styles.btnPrimary : styles.btnSecondary} ${styles.priceCta}`}
                >
                  {tier.ctaLabel}
                </a>
              ) : (
                <Link
                  href={tier.ctaHref}
                  prefetch={false}
                  className={`${tier.featured ? styles.btnPrimary : styles.btnSecondary} ${styles.priceCta}`}
                >
                  {tier.ctaLabel}
                </Link>
              )}
              <ul className={styles.priceFeatures}>
                {tier.features.map((feature) => (
                  <li key={feature} className={styles.priceFeature}>
                    <span className={styles.priceCheck}>
                      <HomeIcon name="check" />
                    </span>
                    {feature}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>

        <p className={styles.priceNote}>{note}</p>
      </div>
    </section>
  );
}
