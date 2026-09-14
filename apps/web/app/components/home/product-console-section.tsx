import { HomeIcon } from './home-icons';
import { ScrollReveal } from './scroll-reveal';
import {
  CONSOLE_PREVIEW_NOTE,
  consoleCallouts,
  consoleIncidents,
  consoleMetrics,
  consoleNav,
  ROUTES,
  type ConsoleMetric,
} from './home-data';
import styles from './home.module.css';

const METRIC_NOTE_CLASS: Record<ConsoleMetric['tone'], string> = {
  green: styles.mNoteGreen,
  red: styles.mNoteRed,
  amber: styles.mNoteAmber,
  blue: styles.mNoteBlue,
};

const SEVERITY_CLASS: Record<string, string> = {
  High: styles.pillHigh,
  Medium: styles.pillMedium,
  Low: styles.pillLow,
};

/**
 * Marketing preview of the security console, presented as a real application
 * frame rather than a floating overlay. The layout mirrors the authenticated
 * product (sidebar + metric tiles + incident table + activity chart) but the
 * figures are illustrative: the frame carries a "Product preview" chip and a
 * plain-language caption underneath, so it is never read as live customer data.
 */
export function ProductConsoleSection() {
  return (
    <section className={styles.section} id="console">
      <div className={styles.consoleInner}>
        <div className={styles.consoleHead}>
          <p className={styles.eyebrow}>The security console</p>
          <h2 className={styles.sectionTitle}>One operational view from risk to response.</h2>
          <p className={styles.sectionLead}>
            All the context security teams need to act fast with confidence — telemetry, threats,
            alerts, incidents, response actions and evidence in one workspace.
          </p>
          <div className={styles.consoleActions}>
            <a href={ROUTES.platformAnchor} className={styles.btnSecondary}>
              Explore the platform
            </a>
          </div>
        </div>

        <ScrollReveal className={styles.consoleFrameWrap}>
          <div className={styles.mock} aria-label="Decoda security console preview (illustration)">
            <div className={styles.mockBar}>
              <span className={styles.mockDots} aria-hidden="true">
                <span className={styles.mockDot} />
                <span className={styles.mockDot} />
                <span className={styles.mockDot} />
              </span>
              <span className={styles.mockCrumb}>Decoda · Dashboard</span>
              <span className={styles.mockSearch} aria-hidden="true">Search workspace…</span>
              <span className={styles.mockBadge}>Product preview</span>
            </div>

            <div className={styles.mockBody}>
              <nav className={styles.mockSidebar} aria-hidden="true">
                {consoleNav.map((item, i) => (
                  <span
                    key={item.label}
                    className={`${styles.mockNavItem}${i === 0 ? ` ${styles.mockNavActive}` : ''}`}
                  >
                    <HomeIcon name={item.icon} />
                    {item.label}
                  </span>
                ))}
              </nav>

              <div className={styles.mockMain}>
                <div className={styles.mockMetrics}>
                  {consoleMetrics.map((m) => (
                    <div key={m.label} className={styles.mockMetric}>
                      <div className={styles.mockMetricLabel}>{m.label}</div>
                      <div className={styles.mockMetricVal}>{m.value}</div>
                      <div className={`${styles.mockMetricNote} ${METRIC_NOTE_CLASS[m.tone]}`}>{m.note}</div>
                    </div>
                  ))}
                </div>

                <div className={styles.mockLower}>
                  <div className={styles.mockPanel}>
                    <div className={styles.mockPanelHead}>
                      <span className={styles.mockPanelTitle}>Recent incidents</span>
                      <span className={styles.mockPanelMore}>View all</span>
                    </div>
                    {consoleIncidents.map((row) => (
                      <div key={row.id} className={styles.mockRow}>
                        <div className={styles.mockRowMain}>
                          <div className={styles.mockRowId}>{row.id}</div>
                          <div className={styles.mockRowEvent}>{row.event}</div>
                        </div>
                        <div className={styles.mockRowSide}>
                          <span className={`${styles.mockPill} ${SEVERITY_CLASS[row.severity]}`}>{row.severity}</span>
                          <span className={styles.mockAge}>{row.age}</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className={`${styles.mockPanel} ${styles.mockChart}`}>
                    <div className={styles.mockPanelHead}>
                      <span className={styles.mockPanelTitle}>Threat activity (7d)</span>
                    </div>
                    <div className={styles.mockChartArea}>
                      <svg viewBox="0 0 320 130" fill="none" role="img" aria-label="Threat activity trend (illustration)">
                        <line x1="0" y1="110" x2="320" y2="110" stroke="#e2e8f0" strokeWidth="1" />
                        <line x1="0" y1="74" x2="320" y2="74" stroke="#eef2f7" strokeWidth="1" />
                        <line x1="0" y1="38" x2="320" y2="38" stroke="#eef2f7" strokeWidth="1" />
                        <path
                          d="M4 96 L50 88 L96 92 L142 70 L188 60 L234 34 L300 22 L300 110 L4 110 Z"
                          fill="rgba(185,28,28,0.08)"
                        />
                        <polyline
                          points="4,96 50,88 96,92 142,70 188,60 234,34 300,22"
                          stroke="#b91c1c"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                        <polyline
                          points="4,104 50,100 96,96 142,90 188,84 234,80 300,66"
                          stroke="#1d4ed8"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </div>
                    <div className={styles.mockLegend}>
                      <span className={styles.mockLegendItem}>
                        <span className={`${styles.mockLegendDot} ${styles.legendThreat}`} aria-hidden="true" />
                        Threats
                      </span>
                      <span className={styles.mockLegendItem}>
                        <span className={styles.mockLegendDot} aria-hidden="true" />
                        Alerts
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <p className={styles.mockNote}>{CONSOLE_PREVIEW_NOTE}</p>

          <div className={styles.consoleCallouts}>
            {consoleCallouts.map((callout) => (
              <div key={callout.title} className={styles.callout}>
                <h3 className={styles.calloutTitle}>{callout.title}</h3>
                <p className={styles.calloutDetail}>{callout.detail}</p>
              </div>
            ))}
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
