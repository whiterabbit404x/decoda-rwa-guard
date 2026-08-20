import { HomeIcon } from './home-icons';
import { ROUTES } from './home-data';
import { EnterpriseSecurityPanel } from './enterprise-security-panel';
import styles from './home.module.css';

export function HeroSection() {
  return (
    <section className={styles.hero}>
      <div className={styles.heroInner}>
        <div className={styles.heroLeft}>
          <p className={styles.heroEyebrow}>Security infrastructure for digital finance</p>
          <h1 className={styles.heroTitle}>
            Security infrastructure for blockchain financial systems.
          </h1>
          <p className={styles.heroLead}>
            Decoda helps institutions launch and scale digital asset and real-world asset programs
            with the controls, visibility, and resilience that modern financial infrastructure
            demands.
          </p>

          <div className={styles.heroCtas}>
            <a href={ROUTES.rwaAnchor} className={styles.btnPrimary}>
              Explore RWA Security
              <HomeIcon name="arrowRight" className={styles.btnArrow} />
            </a>
            <a href={ROUTES.demoMailto} className={styles.btnSecondary}>
              Request a demo
            </a>
          </div>
        </div>

        <div className={styles.heroRight}>
          <EnterpriseSecurityPanel />
        </div>
      </div>
    </section>
  );
}
