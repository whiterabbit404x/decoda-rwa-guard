import type { ReactNode } from 'react';

import { HomeIcon } from './home-icons';
import {
  dashboardPreview,
  investigationPreview,
  reportingPreview,
  type PreviewStat,
  type StatTone,
} from './home-data';
import styles from './home.module.css';

const STAT_NOTE_CLASS: Record<StatTone, string> = {
  green: styles.noteGreen,
  red: styles.noteRed,
  amber: styles.noteAmber,
  blue: styles.noteBlue,
};

const LEVEL_CLASS: Record<'High' | 'Medium' | 'Low', string> = {
  High: styles.pillHigh,
  Medium: styles.pillMedium,
  Low: styles.pillLow,
};

/**
 * Reusable framed product preview. The `title` + `caption` describe the
 * screen for everyone (including assistive tech); the inner mock is
 * decorative illustration, so it is marked aria-hidden and never presented
 * as live customer data.
 */
export function ProductPreviewCard({
  title,
  caption,
  ariaLabel,
  children,
}: {
  title: string;
  caption: string;
  ariaLabel: string;
  children: ReactNode;
}) {
  return (
    <figure className={styles.preview}>
      <div className={styles.previewFrame} aria-hidden="true">
        <div className={styles.previewChrome}>
          <span className={styles.previewDots}>
            <span className={styles.previewDot} />
            <span className={styles.previewDot} />
            <span className={styles.previewDot} />
          </span>
          <span className={styles.previewTag}>Representative preview</span>
        </div>
        <div className={styles.previewBody}>{children}</div>
      </div>
      <figcaption className={styles.previewCaption}>
        <span className={styles.previewCaptionTitle}>{title}</span>
        <span className={styles.previewCaptionText}>
          <span className={styles.srOnly}>{ariaLabel}. </span>
          {caption}
        </span>
      </figcaption>
    </figure>
  );
}

function StatTile({ stat }: { stat: PreviewStat }) {
  return (
    <div className={styles.pvTile}>
      <span className={styles.pvTileLabel}>{stat.label}</span>
      <span className={styles.pvTileValue}>{stat.value}</span>
      <span className={`${styles.pvTileNote} ${STAT_NOTE_CLASS[stat.tone]}`}>{stat.note}</span>
    </div>
  );
}

/** Card 1 — dashboard overview. */
export function DashboardPreviewBody() {
  const d = dashboardPreview;
  return (
    <div className={styles.pvApp}>
      <aside className={styles.pvRail}>
        <span className={styles.pvRailMark}>
          <HomeIcon name="shield" />
        </span>
        <span className={styles.pvRailName}>{d.brand}</span>
        <span className={`${styles.pvRailItem} ${styles.pvRailActive}`} />
        <span className={styles.pvRailItem} />
        <span className={styles.pvRailItem} />
        <span className={styles.pvRailItem} />
      </aside>
      <div className={styles.pvMain}>
        <div className={styles.pvMainHead}>{d.crumb}</div>
        <div className={styles.pvStatRow}>
          <div className={styles.pvGauge}>
            <svg viewBox="0 0 44 44" className={styles.pvGaugeRing}>
              <circle cx="22" cy="22" r="18" stroke="rgba(255,255,255,0.1)" strokeWidth="4" fill="none" />
              <circle
                cx="22"
                cy="22"
                r="18"
                stroke="#fbbf24"
                strokeWidth="4"
                fill="none"
                strokeLinecap="round"
                strokeDasharray="113"
                strokeDashoffset="32"
                transform="rotate(-90 22 22)"
              />
            </svg>
            <span className={styles.pvGaugeVal}>{d.riskScore.value}</span>
            <span className={styles.pvGaugeLabel}>{d.riskScore.label}</span>
            <span className={`${styles.pvTileNote} ${styles.noteAmber}`}>{d.riskScore.note}</span>
          </div>
          <div className={styles.pvTiles}>
            {d.stats.map((stat) => (
              <StatTile key={stat.label} stat={stat} />
            ))}
          </div>
        </div>
        <div className={styles.pvChart}>
          <span className={styles.pvChartHead}>Risk over time</span>
          <svg viewBox="0 0 300 66" className={styles.pvSpark} preserveAspectRatio="none">
            <polyline
              points="2,52 40,44 78,48 116,34 154,40 192,26 230,30 268,16 298,20"
              fill="none"
              stroke="#6aa9ff"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M2,52 40,44 78,48 116,34 154,40 192,26 230,30 268,16 298,20 298,66 2,66 Z"
              fill="rgba(96,165,250,0.12)"
            />
          </svg>
        </div>
        <div className={styles.pvScenarios}>
          <span className={styles.pvChartHead}>Top Risk Scenarios</span>
          {d.scenarios.map((row) => (
            <div key={row.label} className={styles.pvScenarioRow}>
              <span className={styles.pvScenarioLabel}>{row.label}</span>
              <span className={`${styles.pvPill} ${LEVEL_CLASS[row.level]}`}>{row.level}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Card 2 — investigation / alert workflow. */
export function InvestigationPreviewBody() {
  const inv = investigationPreview;
  return (
    <div className={styles.pvDoc}>
      <div className={styles.pvMainHead}>{inv.crumb}</div>
      <div className={styles.pvAlert}>
        <span className={styles.pvAlertDot} />
        <span className={styles.pvAlertTitle}>{inv.alertTitle}</span>
        <span className={`${styles.pvPill} ${LEVEL_CLASS[inv.alertLevel]}`}>{inv.alertLevel}</span>
      </div>
      <div className={styles.pvFields}>
        {inv.fields.map((field) => (
          <div key={field.label} className={styles.pvField}>
            <span className={styles.pvFieldLabel}>{field.label}</span>
            <span className={styles.pvFieldValue}>{field.value}</span>
          </div>
        ))}
      </div>
      <div className={styles.pvTimeline}>
        <span className={styles.pvChartHead}>Timeline</span>
        <div className={styles.pvTimelineList}>
          <span className={styles.pvTimelineRail} />
          {inv.timeline.map((row) => (
            <div key={row.time} className={styles.pvTimelineRow}>
              <span className={styles.pvTimelineTime}>{row.time}</span>
              <span className={styles.pvTimelineDetail}>{row.detail}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Card 3 — reporting / audit trail. */
export function ReportingPreviewBody() {
  const rep = reportingPreview;
  return (
    <div className={styles.pvDoc}>
      <div className={styles.pvMainHead}>{rep.crumb}</div>
      <div className={styles.pvReport}>
        <span className={styles.pvReportTitle}>{rep.reportTitle}</span>
        <span className={`${styles.pvPill} ${styles.pillComplete}`}>{rep.reportStatus}</span>
      </div>
      <div className={styles.pvFields}>
        {rep.fields.map((field) => (
          <div key={field.label} className={styles.pvField}>
            <span className={styles.pvFieldLabel}>{field.label}</span>
            <span className={styles.pvFieldValue}>{field.value}</span>
          </div>
        ))}
      </div>
      <div className={styles.pvChecks}>
        {rep.checks.map((label) => (
          <div key={label} className={styles.pvCheckRow}>
            <span className={styles.pvCheckLabel}>{label}</span>
            <span className={styles.pvCheckMark}>
              <HomeIcon name="check" />
            </span>
          </div>
        ))}
      </div>
      <span className={styles.pvDownload}>
        <HomeIcon name="export" />
        Download Report
      </span>
    </div>
  );
}
