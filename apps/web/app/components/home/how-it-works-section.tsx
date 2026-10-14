import { GuardStory } from './guard-story';
import { SECTION_IDS, STORY_STEPS } from './home-data';
import { HomeIcon } from './home-icons';
import { RevealGroup } from './scroll-reveal';
import { SectionHeading } from './site-ui';
import site from './site.module.css';
import styles from './home.module.css';

/**
 * Section C — the incident lifecycle: Detect → Investigate → Review → Respond →
 * Preserve evidence. A compact lifecycle strip introduces the five stages;
 * the scroll-linked story below walks through each one.
 */
export function HowItWorksSection() {
  return (
    <section
      id={SECTION_IDS.howItWorks}
      className={`${site.section} ${site.sectionSoft}`}
      aria-labelledby="how-title"
    >
      <div className={site.container}>
        <SectionHeading
          id="how-title"
          eyebrow="How Guard works"
          title="From First Signal to Verifiable Evidence."
          lead="AI recommends, a deterministic policy engine decides what needs approval, and people authorize. Every step leaves a record."
        />

        <RevealGroup as="ol" className={`${styles.lifecycle} ${site.staggerWide}`} aria-label="Incident lifecycle">
          {STORY_STEPS.map((step, index) => (
            <li key={step.key} className={styles.lifecycleStep}>
              {index > 0 ? <span className={styles.lifecycleLink} aria-hidden="true" /> : null}
              <span className={styles.lifecycleNode}>
                <span className={styles.lifecycleIcon} aria-hidden="true">
                  <HomeIcon name={step.icon} size={16} />
                </span>
                <span className={styles.lifecycleText}>
                  <span className={styles.lifecycleNum}>{String(index + 1).padStart(2, '0')}</span>
                  <span className={styles.lifecycleName}>{step.label}</span>
                </span>
              </span>
            </li>
          ))}
        </RevealGroup>

        <GuardStory />
      </div>
    </section>
  );
}
