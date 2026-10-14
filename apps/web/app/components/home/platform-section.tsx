import { CAPABILITIES, SECTION_IDS } from './home-data';
import { HomeIcon } from './home-icons';
import { Reveal, RevealGroup } from './scroll-reveal';
import site from './site.module.css';
import styles from './home.module.css';

/**
 * The record chain every workspace keeps — the SaaS workflow Guard is built
 * around (asset → monitoring target → telemetry → detection → alert →
 * incident → response → evidence). Shown as a chain because that is what it
 * is: each record links to the one before it.
 */
const RECORD_CHAIN = ['Asset', 'Monitoring target', 'Telemetry', 'Detection', 'Alert', 'Incident', 'Response', 'Evidence'];

/** Section B — what Guard is, as seven capabilities and one connected record. */
export function PlatformSection() {
  return (
    <section id={SECTION_IDS.platform} className={site.section} aria-labelledby="platform-title">
      <div className={site.container}>
        <Reveal className={styles.splitHead}>
          <div>
            <p className={site.eyebrow}>Introducing Decoda RWA Guard</p>
            <h2 id="platform-title" className={site.title}>
              One Security Operations Platform for Tokenized Assets.
            </h2>
          </div>
          <p className={styles.splitLead}>
            Guard brings monitoring, detection, investigation, policy-controlled response and verifiable
            evidence into one workspace — so every decision stays connected to the evidence behind it.
          </p>
        </Reveal>

        <RevealGroup className={`${styles.capGrid} ${site.staggerTight}`}>
          {CAPABILITIES.map((capability) => (
            <article key={capability.title} className={styles.capCard}>
              <div className={styles.capTop}>
                <span className={styles.capIcon}>
                  <HomeIcon name={capability.icon} size={20} />
                </span>
                <span className={styles.capTag}>{capability.tag}</span>
              </div>
              <h3 className={styles.capTitle}>{capability.title}</h3>
              <p className={styles.capBody}>{capability.body}</p>
            </article>
          ))}

          <article className={`${site.surfaceDark} ${styles.chainCard}`}>
            <div className={styles.chainGlow} aria-hidden="true" />
            <p className={styles.chainKicker}>One connected record</p>
            <h3 className={styles.chainTitle}>Every step links to the one before it.</h3>
            <ol className={styles.chain} aria-label="Workspace record chain">
              {RECORD_CHAIN.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ol>
            <a href={`#${SECTION_IDS.howItWorks}`} className={styles.chainLink}>
              See how Guard works
              <HomeIcon name="arrowRight" size={15} />
            </a>
          </article>
        </RevealGroup>
      </div>
    </section>
  );
}
