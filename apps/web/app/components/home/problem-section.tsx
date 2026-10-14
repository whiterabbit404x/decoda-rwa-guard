import { AUTHORIZATION_QUESTIONS, RISKS, SECTION_IDS, VALIDITY_CHECKS } from './home-data';
import { HomeIcon } from './home-icons';
import { Reveal, RevealGroup } from './scroll-reveal';
import { IllustrativeTag } from './site-ui';
import site from './site.module.css';
import styles from './home.module.css';

/**
 * Section A — why transaction visibility is not enough. The visual separates
 * the two questions an institution has to answer: is the transaction valid on
 * the chain (yes), and was it authorized by the institution (unknown to the
 * chain). Its rows arrive in that order once the card is in view.
 */
export function ProblemSection() {
  return (
    <section
      id={SECTION_IDS.problem}
      className={`${site.section} ${site.sectionSoft}`}
      aria-labelledby="problem-title"
    >
      <div className={site.container}>
        <div className={styles.problemGrid}>
          <Reveal className={styles.problemCopy}>
            <p className={site.eyebrow}>The problem</p>
            <h2 id="problem-title" className={site.title}>
              A Valid Blockchain Transaction Can Still Be Unauthorized.
            </h2>
            <p className={site.lead}>
              A transaction can be correctly signed, executed and final — and still fall outside the approvals,
              operational policies and risk limits your institution runs under. The chain verifies the
              transaction. It cannot verify your authority.
            </p>
            <p className={styles.problemNote}>
              Tokenized-asset operations need more than transaction visibility. Security teams have to see what
              changed, judge it against policy, act under control — and show afterwards what was decided.
            </p>
          </Reveal>

          <Reveal variant="scale" className={`${styles.validity} ${site.delay1}`}>
            <div className={styles.validityHead}>
              <span className={site.mono}>Sample admin transaction</span>
              <IllustrativeTag>Illustration</IllustrativeTag>
            </div>

            <div className={styles.validityBlock}>
              <p className={styles.validityLabel}>Valid on-chain</p>
              <ul className={styles.validityList}>
                {VALIDITY_CHECKS.map((item) => (
                  <li key={item} data-kind="ok">
                    <span className={styles.validityIcon}>
                      <HomeIcon name="check" size={14} />
                    </span>
                    {item}
                    <span className={styles.validityState}>Yes</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className={styles.validityDivider}>
              <span>But was it authorized?</span>
            </div>

            <div className={styles.validityBlock}>
              <p className={styles.validityLabel}>Authorized by your institution</p>
              <ul className={styles.validityList}>
                {AUTHORIZATION_QUESTIONS.map((item) => (
                  <li key={item} data-kind="unknown">
                    <span className={styles.validityIcon}>
                      <HomeIcon name="question" size={14} />
                    </span>
                    {item}
                    <span className={styles.validityState}>Unknown</span>
                  </li>
                ))}
              </ul>
            </div>

            <p className={styles.validityFoot}>
              <HomeIcon name="alert" size={14} />
              The ledger records what happened — not whether it should have.
            </p>
          </Reveal>
        </div>

        <RevealGroup as="ul" className={`${styles.riskGrid} ${site.staggerTight}`} aria-label="Operational risk areas">
          {RISKS.map((risk) => (
            <li key={risk.title} className={styles.riskCard}>
              <span className={styles.riskIcon}>
                <HomeIcon name={risk.icon} size={20} />
              </span>
              <h3>{risk.title}</h3>
              <p>{risk.body}</p>
            </li>
          ))}
        </RevealGroup>
      </div>
    </section>
  );
}
