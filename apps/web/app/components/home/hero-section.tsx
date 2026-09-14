import type { LandingSessionHint } from '../../auth-guards';
import { HomeIcon } from './home-icons';
import { heroCapabilities, ROUTES } from './home-data';
import { IncidentWorkflowDemo } from './incident-workflow-demo';
import { StartMonitoringCta } from './start-monitoring-cta';
import styles from './home.module.css';

/**
 * Dark-navy hero. Headline, lead and CTAs are plain server-rendered markup with
 * no animation on them, so they paint on the first frame and never wait for
 * JavaScript. The only motion in this section is the CSS-only incident workflow
 * to the right.
 */
export function HeroSection({ sessionHint }: { sessionHint: LandingSessionHint }) {
  return (
    <section className={`${styles.hero} ${styles.zoneDark}`}>
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
            <StartMonitoringCta sessionHint={sessionHint} withArrow />
            <a href={ROUTES.platformAnchor} className={styles.btnSecondary}>
              Explore the platform
            </a>
          </div>

          <ul className={styles.capRow}>
            {heroCapabilities.map((cap) => (
              <li key={cap.label} className={styles.capItem}>
                <span className={styles.capIcon} aria-hidden="true">
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
