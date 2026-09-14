import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import { lifecycleRibbon, operatingPillars } from './home-data';
import styles from './home.module.css';

/**
 * The Decoda operating layer, framed as four buyer outcomes rather than as a
 * count of application screens. Product breadth belongs further down the page,
 * in the security console preview — this section sells the operating model.
 *
 * ── Two reveal groups, not one ──────────────────────────────────────────────
 *
 * The section is ~800px tall, which is most of a laptop screen. Observing it
 * as a single block meant the trigger fired while the pillars and the ribbon
 * were still below the fold, and the whole sequence finished before the reader
 * ever saw them — the reason this section read as static.
 *
 * So the head (eyebrow, headline, description) and the sequence (the four
 * pillars and the lifecycle ribbon under them) are observed separately. Each
 * plays where the reader's eye actually is, and because the two blocks sit
 * ~200px apart they fire in close succession on a normal scroll and still read
 * as one continuous cascade.
 *
 * The pillars and the ribbon stay in ONE group on purpose: the ribbon is the
 * explanation of the cards above it, so OBSERVE -> DETECT -> INVESTIGATE ->
 * RESPOND -> PROVE must draw while those cards are still arriving.
 */
export function OperatingLayerSection() {
  return (
    <section className={`${styles.section} ${styles.sectionFirst}`} id="operating-layer">
      <div className={styles.sectionInner}>
        {/* eyebrow 0ms · headline 100ms · description 220ms */}
        <ScrollReveal className={`${styles.sectionHeadCenter} ${styles.revealParts} ${styles.opHead}`}>
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
        </ScrollReveal>

        {/* pillars 0/200/400/600ms, then the lifecycle line draws over 1.9s */}
        <ScrollReveal className={`${styles.opStage} ${styles.revealParts}`}>
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
      </div>
    </section>
  );
}
