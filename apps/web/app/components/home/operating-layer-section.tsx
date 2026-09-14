import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import { lifecycleRibbon, operatingPillars } from './home-data';
import styles from './home.module.css';

/**
 * The Decoda operating layer, framed as four buyer outcomes rather than as a
 * count of application screens. Product breadth belongs further down the page,
 * in the security console preview — this section sells the operating model.
 *
 * The whole block is one reveal group (`revealParts`, so it sequences its own
 * descendants instead of moving as a single slab): eyebrow, headline,
 * description, then the four pillars, then the lifecycle ribbon. It runs once
 * on first view and never replays.
 */
export function OperatingLayerSection() {
  return (
    <section className={`${styles.section} ${styles.sectionFirst}`} id="operating-layer">
      <ScrollReveal className={`${styles.sectionInner} ${styles.revealParts} ${styles.opStage}`}>
        <div className={styles.sectionHeadCenter}>
          <p className={`${styles.eyebrow} ${styles.opEyebrow}`}>The Decoda operating layer</p>
          <h2 className={`${styles.sectionTitle} ${styles.opTitle}`}>
            One security operating layer.
            <br />
            From detection to defensible evidence.
          </h2>
          <p className={`${styles.sectionLead} ${styles.opLead}`}>
            Decoda continuously observes security signals, investigates suspicious activity,
            recommends policy-controlled responses and preserves verifiable evidence across the
            incident lifecycle.
          </p>
        </div>

        <div className={styles.pillars}>
          {operatingPillars.map((pillar) => (
            <article key={pillar.id} className={styles.pillar}>
              <div className={styles.pillarHead}>
                <span className={styles.pillarIcon} aria-hidden="true">
                  <HomeIcon name={pillar.icon} />
                </span>
                <span className={styles.pillarNum} aria-hidden="true">{pillar.num}</span>
              </div>
              <h3 className={styles.pillarTitle}>{pillar.title}</h3>
              <p className={styles.pillarOutcome}>{pillar.outcome}</p>
              <ul className={styles.pillarCaps}>
                {pillar.capabilities.map((capability) => (
                  <li key={capability} className={styles.pillarCap}>
                    <span className={styles.pillarCapDot} aria-hidden="true" />
                    {capability}
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>

        <ol className={styles.ribbon} aria-label="Decoda operating lifecycle">
          <li className={styles.ribbonTrack} aria-hidden="true">
            <span className={styles.ribbonTrackFill} />
          </li>
          {lifecycleRibbon.map((phase) => (
            <li key={phase} className={styles.ribbonStep}>
              <span className={styles.ribbonNode} aria-hidden="true" />
              <span className={styles.ribbonLabel}>{phase}</span>
            </li>
          ))}
        </ol>
      </ScrollReveal>
    </section>
  );
}
