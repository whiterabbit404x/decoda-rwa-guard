import { HomeIcon } from './home-icons';
import { roadmapCards } from './home-data';
import styles from './home.module.css';

export function PlatformVisionSection() {
  return (
    <section className={styles.section} id="platform-vision">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>Roadmap preview</p>
          <h2 className={styles.sectionTitle}>
            Decoda&rsquo;s broader platform vision is emerging in phases.
          </h2>
          <p className={styles.sectionLead}>
            RWA Security is the flagship production platform today. Additional company-level security
            capabilities may expand the Decoda platform over time. The capabilities below are planned
            directions, not released products.
          </p>
        </div>

        <div className={styles.roadmapGrid}>
          {roadmapCards.map((card) => (
            <article key={card.title} className={styles.roadmapCard}>
              <div className={styles.roadmapTop}>
                <span className={styles.roadmapIcon}>
                  <HomeIcon name={card.icon} />
                </span>
                <span className={styles.roadmapBadge}>Roadmap</span>
              </div>
              <h3 className={styles.roadmapTitle}>{card.title}</h3>
              <p className={styles.roadmapDetail}>{card.detail}</p>
              <ul className={styles.roadmapList}>
                {card.capabilities.map((cap) => (
                  <li key={cap} className={styles.roadmapListItem}>
                    <span className={styles.roadmapDot} aria-hidden="true" />
                    {cap}
                  </li>
                ))}
              </ul>
              <span className={styles.roadmapPlanned}>Planned capability</span>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
