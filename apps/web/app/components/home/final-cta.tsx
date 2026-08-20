import Link from 'next/link';

import { HomeIcon } from './home-icons';
import { ROUTES } from './home-data';
import styles from './home.module.css';

export function FinalCTA() {
  return (
    <section className={styles.finalCta}>
      <div className={styles.finalInner}>
        <span className={styles.finalIcon}>
          <HomeIcon name="policy" />
        </span>
        <div className={styles.finalBody}>
          <h2 className={styles.finalTitle}>Security operations shouldn&rsquo;t stop at detection.</h2>
          <p className={styles.finalText}>
            Detect the event. Investigate the evidence. Control the response. Preserve the proof.
          </p>
        </div>
        <div className={styles.finalActions}>
          <Link href={ROUTES.startMonitoring} className={styles.btnPrimary} prefetch={false}>
            Start monitoring
            <HomeIcon name="arrowRight" className={styles.btnArrow} />
          </Link>
          <a href={ROUTES.demoMailto} className={styles.btnSecondary}>
            Book a demo
          </a>
        </div>
      </div>
    </section>
  );
}
