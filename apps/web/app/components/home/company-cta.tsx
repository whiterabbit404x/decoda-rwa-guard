import { DecodaLogo, HomeIcon } from './home-icons';
import { ROUTES } from './home-data';
import styles from './home.module.css';

export function CompanyCTA() {
  return (
    <section className={styles.ctaSection} id="contact">
      <div className={styles.ctaInner}>
        <div className={styles.ctaShield} aria-hidden="true">
          <span className={styles.ctaShieldGlow} />
          <DecodaLogo className={styles.ctaShieldMark} />
        </div>

        <div className={styles.ctaBody}>
          <h2 className={styles.ctaTitle}>
            Position Decoda as the trusted security partner behind digital finance growth.
          </h2>
          <p className={styles.ctaText}>
            From day one, we build security into blockchain financial systems — so institutions can
            innovate with confidence and scale with trust.
          </p>
        </div>

        <div className={styles.ctaActions}>
          <a href={ROUTES.demoMailto} className={styles.btnPrimary}>
            Request a demo
            <HomeIcon name="arrowRight" className={styles.btnArrow} />
          </a>
          <a href={ROUTES.platformVisionAnchor} className={styles.btnSecondary}>
            See platform vision
          </a>
        </div>
      </div>
    </section>
  );
}
