import { DECODA_SITE } from '../../public-site-links';
import { ECOSYSTEM, ROUTES, SECTION_IDS } from './home-data';
import { HomeIcon } from './home-icons';
import { Reveal, RevealGroup } from './scroll-reveal';
import { SectionHeading, StatusBadge, TextLink } from './site-ui';
import site from './site.module.css';
import styles from './home.module.css';

const GUARD_POINTS = [
  'Continuous monitoring and alerts',
  'Investigation and AI-assisted triage',
  'Policy-gated, human-authorized response',
  'Tamper-evident, verifiable evidence',
];

/**
 * Section G — Guard in the Decoda ecosystem. Guard is the flagship and gets the
 * full-width card; Vault and Assets carry the status the company website gives
 * them (testnet, not commercially available) and link there. The connected
 * lifecycle is stated as the roadmap it is, not as a production integration.
 */
export function EcosystemSection() {
  const [guard, ...roadmap] = ECOSYSTEM;

  return (
    <section id={SECTION_IDS.ecosystem} className={site.section} aria-labelledby="ecosystem-title">
      <div className={site.container}>
        <SectionHeading
          id="ecosystem-title"
          align="center"
          eyebrow="The Decoda ecosystem"
          title="The Flagship of the Decoda Ecosystem."
          lead="Decoda builds security and operational infrastructure for tokenized finance. Guard is available for pilot evaluation today; Vault and Assets are earlier-stage products that show where the platform is heading."
        />

        <Reveal variant="scale" className={`${site.surfaceDark} ${styles.flagship}`}>
          <div className={styles.flagshipGrid} aria-hidden="true" />
          <div className={styles.flagshipCopy}>
            <div className={styles.flagshipBadges}>
              <span className={styles.flagshipKicker}>{guard.category}</span>
              <StatusBadge tone={guard.tone} size="sm">
                {guard.status}
              </StatusBadge>
            </div>
            <h3 className={styles.flagshipTitle}>{guard.name}</h3>
            <p className={styles.flagshipBody}>{guard.summary}</p>
            <ul className={styles.flagshipPoints}>
              {GUARD_POINTS.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
            <div className={styles.flagshipActions}>
              <a href={ROUTES.requestPilot} className={site.btnPrimary}>
                Request Guard Pilot
                <HomeIcon name="arrowRight" size={16} />
              </a>
              <a href={DECODA_SITE.guard} className={site.btnSecondary}>
                Guard on decodasecurity.com
                <HomeIcon name="arrowUpRight" size={15} />
              </a>
            </div>
          </div>
          <div className={styles.flagshipArt} aria-hidden="true">
            <div className={styles.radar}>
              <div className={styles.radarSweep} />
              <svg viewBox="0 0 200 200" className={styles.radarSvg} focusable="false">
                <circle cx="100" cy="100" r="96" />
                <circle cx="100" cy="100" r="68" />
                <circle cx="100" cy="100" r="40" />
                <path d="M100 4v192M4 100h192" />
              </svg>
              <span className={`${styles.blip} ${styles.blipA}`} />
              <span className={`${styles.blip} ${styles.blipB}`} />
              <span className={`${styles.blip} ${styles.blipAlert}`} />
              <span className={styles.radarCore}>
                <HomeIcon name="shield" size={26} />
              </span>
            </div>
          </div>
        </Reveal>

        <RevealGroup className={`${styles.roadmapGrid} ${site.staggerWide}`}>
          {roadmap.map((product) => (
            <article key={product.key} className={styles.roadmapCard} data-tone={product.tone}>
              <span className={styles.roadmapIcon} data-tone={product.tone} aria-hidden="true">
                <HomeIcon name={product.icon} size={22} />
              </span>
              <div className={styles.roadmapBody}>
                <div className={styles.roadmapMeta}>
                  <span className={styles.roadmapCategory}>{product.category}</span>
                  <StatusBadge tone={product.tone} size="sm">
                    {product.status}
                  </StatusBadge>
                </div>
                <h3 className={styles.roadmapTitle}>{product.name}</h3>
                <p>{product.summary}</p>
                <p className={styles.roadmapDetail}>{product.detail}</p>
                <a href={product.href} className={styles.roadmapLink}>
                  Learn about {product.shortName}
                  <HomeIcon name="arrowUpRight" size={15} />
                </a>
              </div>
            </article>
          ))}
        </RevealGroup>

        <Reveal as="aside" className={styles.roadmapNote}>
          <strong>Roadmap, stated plainly.</strong> Vault and Assets run on public testnets with sample data. They
          are not commercially available, and they are not integrated with Guard today.
        </Reveal>

        <Reveal className={styles.ecosystemLink}>
          <TextLink href={DECODA_SITE.ecosystem}>The complete Decoda ecosystem on decodasecurity.com</TextLink>
        </Reveal>
      </div>
    </section>
  );
}
