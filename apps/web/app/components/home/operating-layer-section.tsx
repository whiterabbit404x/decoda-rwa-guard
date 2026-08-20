import { AgentControlCard } from './agent-control-card';
import { operatingGroups, type AgentGroup } from './home-data';
import styles from './home.module.css';

const GROUP_CLASS: Record<AgentGroup, string> = {
  observe: styles.groupObserve,
  detect: styles.groupDetect,
  respond: styles.groupRespond,
  govern: styles.groupGovern,
};

export function OperatingLayerSection() {
  return (
    <section className={styles.section} id="operating-layer">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHeadCenter}>
          <p className={styles.eyebrow}>The Decoda operating layer</p>
          <h2 className={styles.sectionTitle}>
            One autonomous operational layer.
            <br />
            12 security control planes. One evidence chain.
          </h2>
          <p className={styles.sectionLead}>
            Decoda AI Agent continuously observes security evidence, correlates activity, forms
            investigations, proposes responses and operates within policies controlled by your
            organization.
          </p>
        </div>

        <div className={styles.layerGroups}>
          {operatingGroups.map((group) => (
            <div key={group.id} className={`${styles.group} ${GROUP_CLASS[group.id]}`}>
              <div className={styles.groupHead}>
                <span className={styles.groupTick} aria-hidden="true" />
                <span className={styles.groupLabel}>{group.header}</span>
              </div>
              <div className={styles.groupCards}>
                {group.planes.map((plane) => (
                  <AgentControlCard key={plane.num} plane={plane} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
