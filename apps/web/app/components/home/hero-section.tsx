import Link from 'next/link';

import { HomeIcon } from './home-icons';
import { heroCapabilities, ROUTES } from './home-data';
import { IncidentWorkflowDemo } from './incident-workflow-demo';
import styles from './home.module.css';

export function HeroSection() {
  return (
    <section className={styles.hero}>
      <div className={styles.heroInner}>
        <div className={styles.heroLeft}>
          <p className={styles.heroEyebrow}>Autonomous security operations for RWA</p>
          <h1 className={styles.heroTitle}>
            <span className={styles.heroTitleLine}>Detect threats.</span>{' '}
            <span className={styles.heroTitleLine}>Investigate with evidence.</span>{' '}
            <span className={styles.heroTitleAccent}>Respond under policy.</span>
          </h1>
          <p className={styles.heroLead}>
            Decoda is a real-time RWA security and incident-response platform with evidence-grounded
            AI investigation and policy-controlled automation.
          </p>
          <p className={styles.heroLead2}>
            From on-chain telemetry to incident response and tamper-evident evidence, Decoda operates
            across the entire security lifecycle.
          </p>

          <div className={styles.heroCtas}>
            <Link href={ROUTES.startMonitoring} className={styles.btnPrimary} prefetch={false}>
              Start monitoring
              <HomeIcon name="arrowRight" className={styles.btnArrow} />
            </Link>
            <a href={ROUTES.platformAnchor} className={styles.btnSecondary}>
              Explore the platform
            </a>
          </div>

          <ul className={styles.capRow}>
            {heroCapabilities.map((cap) => (
              <li key={cap.label} className={styles.capItem}>
                <span className={styles.capIcon}>
                  <HomeIcon name={cap.icon} />
                </span>
                {cap.label}
              </li>
            ))}
          </ul>
        </div>

        <div className={styles.heroRight}>
          <IncidentWorkflowDemo />
        </div>
      </div>
    </section>
  );
}
