import { HomeIcon } from './home-icons';
import { workflowSteps, type WorkflowTone } from './home-data';
import styles from './home.module.css';

const TONE_CLASS: Record<WorkflowTone, string> = {
  critical: styles.wfCritical,
  complete: styles.wfComplete,
  ai: styles.wfAi,
  policy: styles.wfPolicy,
  verified: styles.wfVerified,
};

/**
 * The hero's right-hand panel and the page's primary animation: one incident
 * moving through DETECT -> INVESTIGATE -> EVIDENCE -> RECOMMEND -> POLICY -> PROVE.
 *
 * The motion is entirely CSS (see `home.module.css`), so this stays a server
 * component: the markup below is the finished workflow, and the animation only
 * withholds each stage until its moment on the loop. That also means a visitor
 * who has asked for reduced motion — where every animation is switched off —
 * reads the completed workflow with nothing missing.
 *
 * The content is illustrative sample data and is labelled "EXAMPLE INCIDENT
 * WORKFLOW" so it is never mistaken for live production incidents or customer
 * evidence.
 */
export function IncidentWorkflowDemo() {
  return (
    <aside className={styles.wf} aria-label="Example incident workflow (illustration)">
      <div className={styles.wfHead}>
        <span className={styles.wfTag}>EXAMPLE INCIDENT WORKFLOW</span>
        <span className={styles.wfBadge}>Illustration</span>
      </div>

      <div className={styles.wfList}>
        <span className={styles.wfRail} aria-hidden="true">
          <span className={styles.wfRailFill} />
        </span>

        <div className={styles.wfRows}>
          {workflowSteps.map((step) => (
            <div key={step.title} className={`${styles.wfRow} ${TONE_CLASS[step.tone]}`}>
              <span className={styles.wfIcon} aria-hidden="true">
                <HomeIcon name={step.icon} />
              </span>
              <div className={styles.wfBody}>
                <span className={styles.wfPhase}>{step.phase}</span>
                <div className={styles.wfTitle}>{step.title}</div>
                <div className={styles.wfDetail}>{step.detail}</div>
              </div>
              <div className={styles.wfResult}>{step.result}</div>
            </div>
          ))}
        </div>
      </div>

      <p className={styles.wfFoot}>
        <span className={styles.wfFootDot} aria-hidden="true" />
        Every stage stays linked to the evidence that produced it.
      </p>
    </aside>
  );
}
