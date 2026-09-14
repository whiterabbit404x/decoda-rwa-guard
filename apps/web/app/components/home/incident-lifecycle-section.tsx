import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import { lifecycleStages, type LifecycleTone } from './home-data';
import styles from './home.module.css';

const TONE_CLASS: Record<LifecycleTone, string> = {
  cyan: styles.lcCyan,
  blue: styles.lcBlue,
  indigo: styles.lcIndigo,
  purple: styles.lcPurple,
  amber: styles.lcAmber,
  green: styles.lcGreen,
};

/**
 * Horizontal enterprise process diagram on desktop, vertical on mobile. The
 * connecting track reveals once, the first time the section enters the
 * viewport — it never loops.
 */
export function IncidentLifecycleSection() {
  return (
    <section className={styles.section} id="lifecycle">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>Incident lifecycle</p>
          <h2 className={styles.sectionTitle}>
            From a blockchain signal to defensible evidence.
          </h2>
          <p className={styles.sectionLead}>
            Every conclusion remains connected to the telemetry, transactions, findings, approvals
            and response actions that produced it.
          </p>
        </div>

        <ScrollReveal as="ol" className={styles.lcFlow}>
          <li className={styles.lcTrack} aria-hidden="true">
            <span className={styles.lcTrackFill} />
          </li>

          {lifecycleStages.map((stage, idx) => (
            <li key={stage.title} className={`${styles.lcStageWrap} ${TONE_CLASS[stage.tone]}`}>
              <span className={styles.lcStageIcon} aria-hidden="true">
                <HomeIcon name={stage.icon} />
              </span>
              <div className={styles.lcStageBody}>
                <span className={styles.lcStep}>{`STEP ${idx + 1}`}</span>
                <h3 className={styles.lcStageTitle}>{stage.title}</h3>
                <p className={styles.lcStageDetail}>{stage.detail}</p>
              </div>
            </li>
          ))}
        </ScrollReveal>
      </div>
    </section>
  );
}
