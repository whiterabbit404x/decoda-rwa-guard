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
 * The hero's right-hand panel. This is illustrative sample content —
 * it is explicitly labelled "EXAMPLE INCIDENT WORKFLOW" so it is never
 * mistaken for live production incidents or customer evidence.
 */
export function IncidentWorkflowDemo() {
  return (
    <aside className={styles.wf} aria-label="Example incident workflow (illustration)">
      <div className={styles.wfHead}>
        <span className={styles.wfTag}>EXAMPLE INCIDENT WORKFLOW</span>
        <span className={styles.wfBadge}>Illustration</span>
      </div>
      <div className={styles.wfList}>
        <span className={styles.wfRail} aria-hidden="true" />
        {workflowSteps.map((step) => (
          <div key={step.title} className={`${styles.wfRow} ${TONE_CLASS[step.tone]}`}>
            <span className={styles.wfIcon}>
              <HomeIcon name={step.icon} />
            </span>
            <div className={styles.wfBody}>
              <div className={styles.wfTitle}>{step.title}</div>
              <div className={styles.wfDetail}>{step.detail}</div>
            </div>
            <div className={styles.wfResult}>{step.result}</div>
          </div>
        ))}
      </div>
    </aside>
  );
}
