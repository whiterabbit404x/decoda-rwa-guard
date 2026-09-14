import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import { evidencePillars, type EvidencePillar } from './home-data';
import styles from './home.module.css';

const TONE_CLASS: Record<EvidencePillar['tone'], string> = {
  blue: styles.evBlue,
  green: styles.evGreen,
  amber: styles.evAmber,
};

/** Institutional trust section — deliberately static, no decorative AI imagery. */
export function EvidenceAISection() {
  return (
    <section className={styles.section} id="evidence-ai">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>Evidence-grounded AI</p>
          <h2 className={styles.sectionTitle}>AI that has to show its evidence.</h2>
          <p className={styles.sectionLead}>
            Decoda AI is an autonomous operational layer, not a black-box chatbot. Every finding,
            action and export stays traceable to the data and policy behind it.
          </p>
        </div>

        <ScrollReveal className={styles.evGrid} stagger>
          {evidencePillars.map((pillar) => (
            <article key={pillar.title} className={`${styles.evCard} ${TONE_CLASS[pillar.tone]}`}>
              <span className={styles.evIcon} aria-hidden="true">
                <HomeIcon name={pillar.icon} />
              </span>
              <h3 className={styles.evTitle}>{pillar.title}</h3>
              <p className={styles.evDetail}>{pillar.detail}</p>
            </article>
          ))}
        </ScrollReveal>
      </div>
    </section>
  );
}
