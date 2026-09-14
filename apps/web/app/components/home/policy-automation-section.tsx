import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import { policyLanes, type PolicyLaneId, type PolicyStepTone } from './home-data';
import styles from './home.module.css';

const TONE_CLASS: Record<PolicyStepTone, string> = {
  blue: styles.polBlue,
  amber: styles.polAmber,
  gate: styles.polGate,
  green: styles.polGreen,
};

const LANE_CLASS: Partial<Record<PolicyLaneId, string>> = {
  control: styles.polLaneControl,
};

/**
 * Makes the autonomous/human boundary visible: three lanes, with the policy and
 * approval lane styled as the loudest of the three because it is the point of
 * the section. The steps fade in once, lane by lane, and then stay put.
 */
export function PolicyAutomationSection() {
  return (
    <section className={styles.section} id="policy-automation">
      <div className={styles.sectionInner}>
        <ScrollReveal className={`${styles.sectionHead} ${styles.revealHead}`} stagger>
          <p className={styles.eyebrow}>Autonomous where safe</p>
          <h2 className={styles.sectionTitle}>Human-controlled where necessary.</h2>
          <p className={styles.sectionLead}>
            Decoda observes, investigates and recommends automatically — but high-impact actions
            pass through policy evaluation and human approval before anything executes.
          </p>
        </ScrollReveal>

        <ScrollReveal className={`${styles.polLanes} ${styles.revealParts}`}>
          {policyLanes.map((lane) => (
            <div key={lane.id} className={`${styles.polLane} ${LANE_CLASS[lane.id] ?? ''}`}>
              <div className={styles.polLaneHead}>
                <span className={styles.polLaneBadge}>{lane.label}</span>
              </div>
              <p className={styles.polLaneCaption}>{lane.caption}</p>

              <ol className={styles.polSteps}>
                {lane.steps.map((step) => (
                  <li key={step.title} className={styles.polStepWrap}>
                    <div className={`${styles.polStep} ${TONE_CLASS[step.tone]}`}>
                      <span className={styles.polIcon} aria-hidden="true">
                        <HomeIcon name={step.icon} />
                      </span>
                      <span className={styles.polTitle}>{step.title}</span>
                      <span className={styles.polDetail}>{step.detail}</span>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </ScrollReveal>
      </div>
    </section>
  );
}
