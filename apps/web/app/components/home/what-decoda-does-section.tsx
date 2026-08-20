import { HomeIcon } from './home-icons';
import { capabilityCards } from './home-data';
import styles from './home.module.css';

export function WhatDecodaDoesSection() {
  return (
    <section className={styles.section} id="company">
      <div className={styles.sectionInner}>
        <div className={styles.sectionHead}>
          <p className={styles.eyebrow}>What Decoda does</p>
          <h2 className={styles.sectionTitle}>
            Builds the security layer between blockchain innovation and institutional trust.
          </h2>
        </div>

        <div className={styles.cardGrid3}>
          {capabilityCards.map((card) => (
            <article key={card.title} className={styles.capCard}>
              <span className={styles.capCardIcon}>
                <HomeIcon name={card.icon} />
              </span>
              <div className={styles.capCardBody}>
                <h3 className={styles.capCardTitle}>{card.title}</h3>
                <p className={styles.capCardDetail}>{card.detail}</p>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
