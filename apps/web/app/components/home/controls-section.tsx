import { CONTROLS, PILOT_ALLOWED, PILOT_REFUSED, SECTION_IDS } from './home-data';
import { HomeIcon } from './home-icons';
import { Reveal, RevealGroup } from './scroll-reveal';
import { StatusBadge } from './site-ui';
import site from './site.module.css';
import styles from './home.module.css';

/**
 * Section F — institutional controls. Every control listed is implemented in
 * this repository today (see home-data.ts for the source of each). The pilot
 * boundary beside them is docs/PILOT_EXECUTION_BOUNDARY.md §1: a pilot keeps
 * the whole security workflow and loses exactly one capability.
 */
export function ControlsSection() {
  return (
    <section
      id={SECTION_IDS.controls}
      className={`${site.section} ${site.sectionSoft}`}
      aria-labelledby="controls-title"
    >
      <div className={site.container}>
        <div className={styles.controlsGrid}>
          <Reveal className={styles.controlsIntro}>
            <p className={site.eyebrow}>Institutional controls</p>
            <h2 id="controls-title" className={site.title}>
              Built for Security Teams That Need Control.
            </h2>
            <p className={site.lead}>
              Guard is designed to observe, investigate and record — not to hold your keys or act on its own.
              Every control here is implemented today, and the limits of a pilot are enforced server-side, not
              left to a setting.
            </p>
            <p className={styles.legend}>
              <StatusBadge tone="implemented" size="sm">
                Implemented
              </StatusBadge>
              <span>In the product today — not a roadmap item.</span>
            </p>
          </Reveal>

          <div className={styles.controlsLists}>
            <RevealGroup as="ul" className={`${styles.controlList} ${site.staggerTight}`}>
              {CONTROLS.map((control) => (
                <li key={control.title} className={styles.controlRow}>
                  <span className={styles.controlIcon}>
                    <HomeIcon name={control.icon} size={18} />
                  </span>
                  <div>
                    <h3>{control.title}</h3>
                    <p>{control.body}</p>
                  </div>
                  <StatusBadge tone="implemented" size="sm" className={styles.controlBadge}>
                    Implemented
                  </StatusBadge>
                </li>
              ))}
            </RevealGroup>

            <Reveal className={styles.boundary} aria-labelledby="boundary-title">
              <div className={styles.boundaryHead}>
                <p className={site.eyebrow}>The pilot boundary</p>
                <h3 id="boundary-title">What a pilot can — and cannot — do.</h3>
              </div>
              <div className={styles.boundaryCols}>
                <div>
                  <p className={styles.boundaryLabel}>During a pilot, your team can</p>
                  <ul className={styles.boundaryList} data-kind="allowed">
                    {PILOT_ALLOWED.map((item) => (
                      <li key={item}>
                        <HomeIcon name="check" size={14} />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className={styles.boundaryLabel}>Guard will refuse to</p>
                  <ul className={styles.boundaryList} data-kind="refused">
                    {PILOT_REFUSED.map((item) => (
                      <li key={item}>
                        <HomeIcon name="minus" size={14} />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <p className={styles.boundaryFoot}>
                Every refusal is written to the hash-chained audit log. Decoda never holds customer private keys.
              </p>
            </Reveal>
          </div>
        </div>
      </div>
    </section>
  );
}
