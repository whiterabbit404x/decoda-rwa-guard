import { HomeIcon } from './home-icons';
import { lifecycleSteps } from './home-data';
import styles from './home.module.css';

export function SecurityLifecycleSection() {
  return (
    <section className={styles.section} id="how-it-works">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>How RWA Security works</p>
          <h2 className={styles.sectionTitle}>
            One operational lifecycle, from first signal to defensible proof.
          </h2>
        </div>

        <ol className={styles.lcFlow}>
          {lifecycleSteps.map((step, idx) => (
            <li key={step.title} className={styles.lcStageWrap}>
              <div className={styles.lcStage}>
                <span className={styles.lcStageIcon}>
                  <HomeIcon name={step.icon} />
                </span>
                <span className={styles.lcStageTitle}>{step.title}</span>
                <span className={styles.lcStageDetail}>{step.detail}</span>
              </div>
              {idx < lifecycleSteps.length - 1 && (
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
