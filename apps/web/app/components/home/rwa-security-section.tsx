import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import { rwaCards } from './home-data';
import styles from './home.module.css';

export function RWASecuritySection() {
  return (
    <section className={styles.section} id="rwa-security">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>Built for tokenized assets</p>
          <h2 className={styles.sectionTitle}>
            Security controls for the operational risks unique to RWA.
          </h2>
        </div>

        <ScrollReveal className={styles.cardGrid4} stagger>
          {rwaCards.map((card) => (
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
