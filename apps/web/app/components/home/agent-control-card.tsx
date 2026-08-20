import { HomeIcon } from './home-icons';
import type { AgentControlPlane, AgentState } from './home-data';
import styles from './home.module.css';

const STATE_CLASS: Record<AgentState, string> = {
  continuous: styles.stCont,
  'on-event': styles.stEvent,
  policy: styles.stPolicy,
  verified: styles.stVerified,
};

export function AgentControlCard({ plane }: { plane: AgentControlPlane }) {
  return (
    <article className={`${styles.aCard} ${STATE_CLASS[plane.state]}`}>
      <div className={styles.aTop}>
        <span className={styles.aNum}>{plane.num}</span>
        <span className={styles.aIcon}>
          <HomeIcon name={plane.icon} />
        </span>
      </div>
      <div className={styles.aScreen}>{plane.screen}</div>
      <div className={styles.aAgent}>{plane.agent}</div>
      <p className={styles.aDesc}>{plane.description}</p>
      <div className={styles.aFoot}>
        <span className={styles.aDot} aria-hidden="true" />
        <span className={styles.aState}>{plane.stateLabel}</span>
      </div>
    </article>
  );
}
