import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import { teamCards } from './home-data';
import styles from './home.module.css';

export function TeamsSection() {
  return (
    <section className={styles.section} id="teams">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>Built for teams</p>
          <h2 className={styles.sectionTitle}>
            Built for teams responsible for tokenized financial infrastructure.
          </h2>
        </div>

        <ScrollReveal className={styles.cardGrid3} stagger>
          {teamCards.map((card) => (
            <article key={card.title} className={styles.featureCard}>
              <span className={styles.featureIcon} aria-hidden="true">
                <HomeIcon name={card.icon} />
              </span>
              <h3 className={styles.featureTitle}>{card.title}</h3>
              <p className={styles.featureDetail}>{card.detail}</p>
            </article>
          ))}
        </ScrollReveal>
      </div>
    </section>
  );
}
