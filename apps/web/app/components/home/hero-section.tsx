import { GuardConsole } from './guard-console';
import { GUARD_STATUS, HERO_PRINCIPLES, HERO_VALUES, ROUTES, SECTION_IDS } from './home-data';
import { HomeIcon } from './home-icons';
import { RevealGroup } from './scroll-reveal';
import { AmbientBackdrop } from './site-ui';
import site from './site.module.css';
import styles from './home.module.css';

/**
 * The hero. Headline, lead and calls to action are plain server-rendered
 * markup: they paint on the first frame and never wait for JavaScript. Their
 * entrance is CSS only — each of the three headline lines rises out of its own
 * mask, then the lead, the value line and the CTAs follow — so it plays
 * without JavaScript and is switched off under reduced motion. The calls to
 * action are the same for every visitor: a signed-in customer finds
 * "Dashboard" in the header.
 */
export function HeroSection() {
  return (
    <section className={styles.hero} aria-labelledby="hero-title">
      <AmbientBackdrop mint />
      <svg
        className={styles.heroLines}
        viewBox="0 0 1200 600"
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M-20 420 C 240 380, 360 300, 560 310 S 900 220, 1220 120" />
        <path d="M-20 520 C 260 470, 420 420, 640 430 S 960 360, 1220 300" />
      </svg>

      <div className={`${site.container} ${styles.heroInner}`}>
        <div className={styles.heroCopy}>
          <p className={styles.heroPill}>
            <span className={styles.heroPillDot} aria-hidden="true" />
            <span className={styles.heroPillName}>Decoda RWA Guard</span>
            <span className={styles.heroPillSep} aria-hidden="true" />
            <span>{GUARD_STATUS}</span>
          </p>

          <h1 id="hero-title" className={styles.heroTitle}>
            <span className={styles.line}>
              <span>Protect the</span>
            </span>{' '}
            <span className={styles.line}>
              <span>Infrastructure Behind</span>
            </span>{' '}
            <span className={styles.line}>
              <span className={site.accentText}>Tokenized Finance.</span>
            </span>
          </h1>

          <p className={styles.heroLead}>
            Decoda RWA Guard brings security monitoring, evidence-grounded investigation, and policy-controlled
            response workflows together for tokenized financial infrastructure.
          </p>

          <p className={styles.valueLine}>
            {HERO_VALUES.map((value) => (
              <span key={value.text} className={styles.valueItem}>
                <HomeIcon name={value.icon} size={17} />
                {value.text}
              </span>
            ))}
          </p>

          <div className={styles.heroActions}>
            <a href={ROUTES.requestPilot} className={`${site.btnPrimary} ${site.btnLg}`}>
              Request Guard Pilot
              <HomeIcon name="arrowRight" size={17} />
            </a>
            <a href={`#${SECTION_IDS.howItWorks}`} className={`${site.btnSecondary} ${site.btnLg}`}>
              Explore How Guard Works
            </a>
          </div>
        </div>

        <div className={styles.heroVisual}>
          <GuardConsole />
        </div>
      </div>

      <div className={site.container}>
        <RevealGroup as="ul" className={`${styles.principles} ${site.staggerTight}`} aria-label="Product principles">
          {HERO_PRINCIPLES.map((principle) => (
            <li key={principle.title} className={styles.principle}>
              <span className={styles.principleIcon}>
                <HomeIcon name={principle.icon} size={18} />
              </span>
              <span>
                <strong>{principle.title}</strong>
                <span>{principle.body}</span>
              </span>
            </li>
          ))}
        </RevealGroup>
      </div>
    </section>
  );
}
