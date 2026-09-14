import type { LandingSessionHint } from '../../auth-guards';
import { HomeIcon } from './home-icons';
import { ROUTES } from './home-data';
import { StartMonitoringCta } from './start-monitoring-cta';
import styles from './home.module.css';

export function FinalCTA({ sessionHint }: { sessionHint: LandingSessionHint }) {
  return (
    <section className={`${styles.finalCta} ${styles.zoneDark}`}>
      <div className={styles.finalInner}>
        <span className={styles.finalIcon} aria-hidden="true">
          <HomeIcon name="policy" />
        </span>
        <h2 className={styles.finalTitle}>Security operations shouldn&rsquo;t stop at detection.</h2>
        <p className={styles.finalText}>
          Detect the event. Investigate the evidence. Control the response. Preserve the proof.
        </p>
        <div className={styles.finalActions}>
          <StartMonitoringCta sessionHint={sessionHint} withArrow />
          <a href={ROUTES.demoMailto} className={styles.btnSecondary}>
            Book a demo
          </a>
        </div>
      </div>
    </section>
  );
}
