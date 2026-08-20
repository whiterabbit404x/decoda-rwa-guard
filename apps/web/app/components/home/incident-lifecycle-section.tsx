import { HomeIcon } from './home-icons';
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

        <ol className={styles.lcFlow}>
          {lifecycleStages.map((stage, idx) => (
            <li key={stage.title} className={styles.lcStageWrap}>
              <div className={`${styles.lcStage} ${TONE_CLASS[stage.tone]}`}>
                <span className={styles.lcStageIcon}>
                  <HomeIcon name={stage.icon} />
                </span>
                <span className={styles.lcStageTitle}>{stage.title}</span>
                <span className={styles.lcStageDetail}>{stage.detail}</span>
              </div>
              {idx < lifecycleStages.length - 1 && (
                <span className={styles.lcArrow} aria-hidden="true">
                  <HomeIcon name="arrowRight" />
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
