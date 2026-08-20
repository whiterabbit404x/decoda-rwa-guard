import { HomeIcon } from './home-icons';
import { policySteps, type PolicyStepTone } from './home-data';
import styles from './home.module.css';

const TONE_CLASS: Record<PolicyStepTone, string> = {
  blue: styles.polBlue,
  amber: styles.polAmber,
  gate: styles.polGate,
  green: styles.polGreen,
};

export function PolicyAutomationSection() {
  return (
    <section className={styles.section} id="policy-automation">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>Autonomous where safe</p>
          <h2 className={styles.sectionTitle}>Human-controlled where necessary.</h2>
          <p className={styles.sectionLead}>
            Decoda observes, investigates and recommends automatically — but high-impact actions
            pass through policy evaluation and human approval before anything executes.
          </p>
        </div>

        <ol className={styles.polFlow}>
          {policySteps.map((step, idx) => (
            <li key={step.title} className={styles.polStepWrap}>
              <div className={`${styles.polStep} ${TONE_CLASS[step.tone]}`}>
                <span className={styles.polIcon}>
                  <HomeIcon name={step.icon} />
                </span>
                <span className={styles.polTitle}>{step.title}</span>
                <span className={styles.polDetail}>{step.detail}</span>
              </div>
              {idx < policySteps.length - 1 && (
                <span className={styles.polArrow} aria-hidden="true">
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
