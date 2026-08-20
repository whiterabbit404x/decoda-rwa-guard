import { HomeIcon } from './home-icons';
import { whyControlPoints } from './home-data';
import styles from './home.module.css';

export function WhyThisMattersSection() {
  return (
    <section className={styles.section}>
      <div className={`${styles.sectionInner} ${styles.whyInner}`}>
        <div className={styles.whyLeft}>
          <p className={styles.eyebrow}>Why this matters</p>
          <h2 className={styles.sectionTitle}>
            Blockchain financial infrastructure expands the blast radius of weak controls.
          </h2>
          <p className={styles.sectionLead}>
            In traditional finance, failures impact products, services, or single business lines. In
            blockchain financial systems, security failures can trigger downtime, regulatory
            scrutiny, and reputational loss across counterparties and investors.
          </p>
        </div>

        <div className={styles.whyPanel}>
          <ul className={styles.whyList}>
            {whyControlPoints.map((point) => (
              <li key={point} className={styles.whyItem}>
                <span className={styles.whyCheck}>
                  <HomeIcon name="check" />
                </span>
                <span className={styles.whyText}>{point}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
