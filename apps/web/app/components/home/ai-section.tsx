import { AI_CANNOT, AI_POINTS, SECTION_IDS } from './home-data';
import { HomeIcon } from './home-icons';
import { Reveal, RevealGroup } from './scroll-reveal';
import site from './site.module.css';
import styles from './home.module.css';

/**
 * Section E — evidence-grounded AI.
 *
 * Scope of the claims (docs/AI_TRIAGE_OPENAI.md): an optional, per-deployment
 * triage layer that summarizes incidents, builds evidence-linked timelines and
 * recommends PREDEFINED runbooks. It has no tools, never acts on-chain, and a
 * recommendation needs a person to approve it — which records a decision and
 * executes nothing. Policy decisions are made by the deterministic engine.
 * Nothing here implies autonomous execution or AI control of customer assets.
 */
export function AiSection() {
  return (
    <section id={SECTION_IDS.ai} className={site.section} aria-labelledby="ai-title">
      <div className={site.container}>
        <div className={styles.aiGrid}>
          <div className={styles.aiCopy}>
            <Reveal>
              <p className={site.eyebrow}>Evidence-grounded AI</p>
              <h2 id="ai-title" className={site.title}>
                AI That Explains Its Findings.
              </h2>
              <p className={site.lead}>
                Guard&rsquo;s AI-assisted triage works from the incident record — telemetry, detections,
                transactions and timeline — and points to the evidence behind every statement. It drafts; it
                recommends. Deterministic policy and your people decide.
              </p>
            </Reveal>

            <RevealGroup as="ul" className={`${styles.aiPoints} ${site.staggerTight}`}>
              {AI_POINTS.map((point) => (
                <li key={point.title}>
                  <span className={styles.aiPointIcon}>
                    <HomeIcon name={point.icon} size={17} />
                  </span>
                  <span>
                    <strong>{point.title}</strong>
                    <span>{point.body}</span>
                  </span>
                </li>
              ))}
            </RevealGroup>
          </div>

          <Reveal variant="right" className={styles.aiVisualWrap}>
            <figure className={styles.aiFigure}>
              <figcaption className={site.srOnly}>
                Illustration with sample data: an AI-assisted incident summary that cites numbered evidence,
                recommends a predefined runbook, and waits for a deterministic policy decision and human approval.
              </figcaption>
              <div className={`${site.surfaceDark} ${styles.aiPanel}`} aria-hidden="true">
                <div className={styles.aiPanelHead}>
                  <span className={styles.aiPanelTitle}>
                    <HomeIcon name="spark" size={15} />
                    AI-assisted triage
                  </span>
                  <span className={styles.aiDraft}>Draft · recommend-only</span>
                </div>

                <p className={styles.aiSummary}>
                  A privileged role was granted to an operator outside the approved change window
                  <span className={styles.cite}>E1</span>, and the same operator queued a supply change 38 seconds
                  later <span className={styles.cite}>E2</span>. Both fall under the privileged-change policy
                  <span className={styles.cite}>E3</span>.
                </p>

                <ol className={styles.aiEvidence}>
                  <li>
                    <span className={styles.cite}>E1</span>
                    <span>RoleGranted · Mint controller</span>
                    <code>block · tx sample</code>
                  </li>
                  <li>
                    <span className={styles.cite}>E2</span>
                    <span>Supply change queued</span>
                    <code>T+38s</code>
                  </li>
                  <li>
                    <span className={styles.cite}>E3</span>
                    <span>Policy · privileged change window</span>
                    <code>rule</code>
                  </li>
                </ol>

                <div className={styles.aiDecision}>
                  <div>
                    <small>Recommended runbook</small>
                    <strong>Revoke role via issuer multisig</strong>
                  </div>
                  <div>
                    <small>Policy · deterministic</small>
                    <strong className={styles.aiWarn}>Approval required</strong>
                  </div>
                  <div>
                    <small>Human approval</small>
                    <strong>Awaiting 2 approvers</strong>
                  </div>
                </div>

                <div className={styles.aiCannot}>
                  <small>The AI cannot</small>
                  <ul>
                    {AI_CANNOT.map((item) => (
                      <li key={item}>
                        <HomeIcon name="minus" size={12} />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <p className={styles.figureNote} aria-hidden="true">
                Illustrative · sample data
              </p>
            </figure>
          </Reveal>
        </div>

        <Reveal as="p" className={styles.aiFootnote}>
          AI-assisted triage is optional and enabled per deployment. It has no tools that act on-chain, and
          approving one of its recommendations records a decision — it executes nothing on its own.
        </Reveal>
      </div>
    </section>
  );
}
