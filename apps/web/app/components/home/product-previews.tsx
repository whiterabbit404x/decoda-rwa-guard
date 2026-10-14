import type { ReactNode } from 'react';

import { DecodaShield } from './decoda-logo';
import { PRODUCT_SCREENS, type ScreenKey } from './home-data';
import { HomeIcon } from './home-icons';
import styles from './showcase.module.css';

/**
 * Illustrative previews of the nine Guard screens.
 *
 * These are drawings of the product's layout — navy navigation beside a light
 * workspace — filled with SAMPLE data. They are not screenshots: no sanitized
 * screenshots of a customer workspace exist to show, and a recording of a
 * seeded workspace would present simulator data as if it were evidence. So
 * every preview is labelled as an illustration, the values are static (no
 * count-ups, no ticking clocks), and the states deliberately include what the
 * product shows when data is missing or stale — never only the happy path.
 */

type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral' | 'accent';

function Chip({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={styles.mChip} data-tone={tone}>
      {children}
    </span>
  );
}

function Tile({ label, value, note, tone = 'neutral' }: { label: string; value: string; note: string; tone?: Tone }) {
  return (
    <div className={styles.mTile}>
      <span className={styles.mTileLabel}>{label}</span>
      <span className={styles.mTileValue}>{value}</span>
      <span className={styles.mTileNote} data-tone={tone}>
        {note}
      </span>
    </div>
  );
}

function PanelHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={styles.mPanelHead}>
      <strong>{title}</strong>
      {children}
    </div>
  );
}

/* --------------------------------- Screens -------------------------------- */

function DashboardScreen() {
  return (
    <>
      <div className={styles.mPosture}>
        <Chip tone="ok">Monitoring running</Chip>
        <Chip tone="warn">1 source stale</Chip>
        <Chip tone="neutral">Evidence pipeline idle</Chip>
      </div>
      <div className={styles.mTiles}>
        <Tile label="Monitored assets" value="4" note="1 coverage gap" tone="warn" />
        <Tile label="Open alerts" value="3" note="1 high" tone="danger" />
        <Tile label="Open incidents" value="1" note="Investigating" tone="info" />
        <Tile label="Telemetry" value="3 / 4" note="Sources current" tone="warn" />
      </div>
      <div className={styles.mSplit}>
        <div className={styles.mPanel}>
          <PanelHead title="Detections · 7 days" />
          <div className={styles.mBars} aria-hidden="true">
            <span data-h="2" />
            <span data-h="3" />
            <span data-h="1" />
            <span data-h="4" />
            <span data-h="2" />
            <span data-h="6" />
            <span data-h="5" />
          </div>
        </div>
        <div className={styles.mPanel}>
          <PanelHead title="Needs attention" />
          <ul className={styles.mList}>
            <li>
              <span className={styles.mRail} data-tone="danger" />
              <span>
                Privileged role change<small>Mint controller</small>
              </span>
              <Chip tone="danger">High</Chip>
            </li>
            <li>
              <span className={styles.mRail} data-tone="warn" />
              <span>
                Telemetry stale<small>NAV oracle feed</small>
              </span>
              <Chip tone="warn">Stale</Chip>
            </li>
          </ul>
        </div>
      </div>
    </>
  );
}

function AssetsScreen() {
  const rows = [
    { name: 'Treasury token', kind: 'ERC-20', link: 'Linked', linkTone: 'ok' as Tone, risk: 'Low', riskTone: 'ok' as Tone },
    { name: 'Mint controller', kind: 'Admin role', link: 'Linked', linkTone: 'ok' as Tone, risk: 'High', riskTone: 'danger' as Tone },
    { name: 'Redemption queue', kind: 'Contract', link: 'Linked', linkTone: 'ok' as Tone, risk: 'Medium', riskTone: 'warn' as Tone },
    { name: 'NAV oracle', kind: 'Oracle feed', link: 'Not linked', linkTone: 'warn' as Tone, risk: 'Not assessed', riskTone: 'neutral' as Tone },
  ];
  return (
    <div className={styles.mSplitWide}>
      <div className={styles.mPanel}>
        <PanelHead title="Asset registry">
          <Chip tone="neutral">4 assets</Chip>
        </PanelHead>
        <div className={styles.mTable} role="presentation">
          <div className={styles.mTableHead}>
            <span>Asset</span>
            <span>Monitoring</span>
            <span>Risk</span>
          </div>
          {rows.map((row) => (
            <div key={row.name} className={styles.mTableRow}>
              <span>
                {row.name}
                <small>{row.kind}</small>
              </span>
              <span>
                <Chip tone={row.linkTone}>{row.link}</Chip>
              </span>
              <span>
                <Chip tone={row.riskTone}>{row.risk}</Chip>
              </span>
            </div>
          ))}
        </div>
      </div>
      <div className={styles.mPanel}>
        <PanelHead title="Risk assessor" />
        <dl className={styles.mKv}>
          <div>
            <dt>Asset</dt>
            <dd>Mint controller</dd>
          </div>
          <div>
            <dt>Role holders</dt>
            <dd>3 addresses</dd>
          </div>
          <div>
            <dt>Open findings</dt>
            <dd>1 privileged change</dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>
              <Chip tone="ok">Monitored</Chip>
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function SourcesScreen() {
  const sources = [
    { name: 'Contract events', mode: 'Stream', heartbeat: 'ok', poll: 'na', telemetry: 'ok', status: 'Receiving', tone: 'ok' as Tone },
    { name: 'RPC provider', mode: 'Polling', heartbeat: 'ok', poll: 'ok', telemetry: 'ok', status: 'Polled 30s ago', tone: 'ok' as Tone },
    { name: 'NAV oracle feed', mode: 'Polling', heartbeat: 'ok', poll: 'ok', telemetry: 'stale', status: 'Telemetry stale', tone: 'warn' as Tone },
    { name: 'Wallet activity', mode: 'Stream', heartbeat: 'ok', poll: 'na', telemetry: 'none', status: 'No data yet', tone: 'neutral' as Tone },
  ];
  const mark = (value: string) =>
    value === 'ok' ? (
      <span className={styles.mMark} data-tone="ok">
        <HomeIcon name="check" size={12} />
      </span>
    ) : value === 'na' ? (
      <span className={styles.mMark} data-tone="neutral">
        —
      </span>
    ) : (
      <span className={styles.mMark} data-tone={value === 'stale' ? 'warn' : 'neutral'}>
        <HomeIcon name="minus" size={12} />
      </span>
    );
  return (
    <div className={styles.mPanel}>
      <PanelHead title="Monitoring sources">
        <Chip tone="neutral">Heartbeat · poll · telemetry</Chip>
      </PanelHead>
      <div className={styles.mTable} role="presentation">
        <div className={`${styles.mTableHead} ${styles.mSourcesGrid}`}>
          <span>Source</span>
          <span>Heartbeat</span>
          <span>Poll</span>
          <span>Telemetry</span>
          <span>Status</span>
        </div>
        {sources.map((source) => (
          <div key={source.name} className={`${styles.mTableRow} ${styles.mSourcesGrid}`}>
            <span>
              {source.name}
              <small>{source.mode}</small>
            </span>
            <span>{mark(source.heartbeat)}</span>
            <span>{mark(source.poll)}</span>
            <span>{mark(source.telemetry)}</span>
            <span>
              <Chip tone={source.tone}>{source.status}</Chip>
            </span>
          </div>
        ))}
      </div>
      <p className={styles.mFootnote}>A live heartbeat is not current telemetry — each signal is shown on its own.</p>
    </div>
  );
}

function ThreatScreen() {
  const detectors = [
    { name: 'Privileged role change', value: '4', w: '4' },
    { name: 'Large transfer', value: '2', w: '2' },
    { name: 'Mint / burn anomaly', value: '1', w: '1' },
  ];
  return (
    <div className={styles.mSplit}>
      <div className={styles.mPanel}>
        <PanelHead title="Detector activity · 7 days" />
        <ul className={styles.mDetectors}>
          {detectors.map((detector) => (
            <li key={detector.name}>
              <span>{detector.name}</span>
              <span className={styles.mMeter}>
                <span data-w={detector.w} />
              </span>
              <strong>{detector.value}</strong>
            </li>
          ))}
        </ul>
      </div>
      <div className={styles.mPanel}>
        <PanelHead title="Recent detections" />
        <ul className={styles.mList}>
          <li>
            <span className={styles.mRail} data-tone="danger" />
            <span>
              Role granted outside window<small>Mint controller</small>
            </span>
            <Chip tone="danger">Confirmed</Chip>
          </li>
          <li>
            <span className={styles.mRail} data-tone="ok" />
            <span>
              No anomaly in recent evidence<small>Treasury token</small>
            </span>
            <Chip tone="ok">Checked</Chip>
          </li>
          <li>
            <span className={styles.mRail} data-tone="neutral" />
            <span>
              No evidence received<small>Wallet activity</small>
            </span>
            <Chip tone="neutral">No evidence</Chip>
          </li>
        </ul>
      </div>
    </div>
  );
}

function AlertsScreen() {
  const alerts = [
    { title: 'Privileged role granted outside window', entity: 'Mint controller', sev: 'High', tone: 'danger' as Tone, age: '2m' },
    { title: 'Transfer above policy limit', entity: 'Treasury token', sev: 'Medium', tone: 'warn' as Tone, age: '18m' },
    { title: 'New spender approval', entity: 'Issuer multisig', sev: 'Low', tone: 'info' as Tone, age: '1h' },
  ];
  return (
    <div className={styles.mSplitWide}>
      <div className={styles.mPanel}>
        <PanelHead title="Alert queue">
          <Chip tone="neutral">Severity-routed</Chip>
        </PanelHead>
        <ul className={styles.mList}>
          {alerts.map((alert) => (
            <li key={alert.title}>
              <span className={styles.mRail} data-tone={alert.tone} />
              <span>
                {alert.title}
                <small>
                  {alert.entity} · {alert.age}
                </small>
              </span>
              <Chip tone={alert.tone}>{alert.sev}</Chip>
            </li>
          ))}
        </ul>
      </div>
      <div className={`${styles.mPanel} ${styles.mDrawer}`}>
        <PanelHead title="Entity" />
        <dl className={styles.mKv}>
          <div>
            <dt>Name</dt>
            <dd>Mint controller</dd>
          </div>
          <div>
            <dt>Type</dt>
            <dd>Admin role</dd>
          </div>
          <div>
            <dt>Related alerts</dt>
            <dd>2</dd>
          </div>
        </dl>
        <span className={styles.mButton}>Escalate to incident</span>
      </div>
    </div>
  );
}

function IncidentsScreen() {
  const stages = ['Opened', 'Investigating', 'Response', 'Resolved'];
  return (
    <div className={styles.mPanel}>
      <PanelHead title="INC-SAMPLE · Privileged role change">
        <Chip tone="danger">High</Chip>
      </PanelHead>
      <ol className={styles.mLifecycle}>
        {stages.map((stage, index) => (
          <li key={stage} data-state={index < 1 ? 'done' : index === 1 ? 'active' : 'todo'}>
            <span />
            {stage}
          </li>
        ))}
      </ol>
      <div className={styles.mTabsRow}>
        <span data-active="true">Timeline</span>
        <span>Evidence</span>
        <span>Workflow</span>
      </div>
      <ol className={styles.mTimeline}>
        <li>
          <time>T+0s</time>Role granted to unrecognized operator
        </li>
        <li>
          <time>T+38s</time>Supply change queued by the same operator
        </li>
        <li>
          <time>T+1m</time>Two alerts correlated into this incident
        </li>
      </ol>
      <p className={styles.mAi}>
        <HomeIcon name="spark" size={13} /> AI-assisted summary · draft · recommend-only
      </p>
    </div>
  );
}

function ResponseScreen() {
  return (
    <div className={styles.mPanel}>
      <PanelHead title="Revoke privileged role">
        <Chip tone="warn">Approval required</Chip>
      </PanelHead>
      <dl className={styles.mKv}>
        <div>
          <dt>Source</dt>
          <dd>INC-SAMPLE · recommended runbook</dd>
        </div>
        <div>
          <dt>Policy decision</dt>
          <dd>
            <code>privileged_change.requires_approval</code>
          </dd>
        </div>
        <div>
          <dt>Approvals</dt>
          <dd>1 of 2 · step-up MFA</dd>
        </div>
        <div>
          <dt>Execution</dt>
          <dd>
            <Chip tone="accent">
              <HomeIcon name="lock" size={11} /> Recommend-only
            </Chip>
          </dd>
        </div>
      </dl>
      <div className={styles.mActionsRow}>
        <span className={styles.mButton}>Approve</span>
        <span className={`${styles.mButton} ${styles.mButtonGhost}`}>Reject</span>
      </div>
    </div>
  );
}

function EvidenceScreen() {
  const audit = [
    { event: 'evidence.sealed', hash: '5e9a…c3d4' },
    { event: 'action.approved', hash: 'd2f8…0b7e' },
    { event: 'policy.approval_required', hash: 'a03e…6c12' },
  ];
  return (
    <div className={styles.mSplit}>
      <div className={styles.mPanel}>
        <PanelHead title="EV-SAMPLE">
          <Chip tone="ok">Sealed</Chip>
        </PanelHead>
        <dl className={styles.mKv}>
          <div>
            <dt>Integrity</dt>
            <dd>
              <Chip tone="ok">Verified · SHA-256</Chip>
            </dd>
          </div>
          <div>
            <dt>Merkle root</dt>
            <dd>
              <code>9f1c…a72e</code>
            </dd>
          </div>
          <div>
            <dt>Offline check</dt>
            <dd>VERIFY.md included</dd>
          </div>
        </dl>
      </div>
      <div className={styles.mPanel}>
        <PanelHead title="Audit log · hash-chained" />
        <ul className={styles.mAudit}>
          {audit.map((row) => (
            <li key={row.event}>
              <span>{row.event}</span>
              <code>{row.hash}</code>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function HealthScreen() {
  const signals = [
    { name: 'Worker heartbeat', state: 'Alive · 12s ago', tone: 'ok' as Tone },
    { name: 'Monitoring poll', state: 'Ran · 30s ago', tone: 'ok' as Tone },
    { name: 'Telemetry', state: '3 of 4 sources current', tone: 'warn' as Tone },
    { name: 'Evidence pipeline', state: 'Idle', tone: 'neutral' as Tone },
  ];
  return (
    <>
      <div className={styles.mVerdict} data-tone="warn">
        <HomeIcon name="alert" size={16} />
        <span>
          <strong>Degraded</strong>
          <small>Monitoring is running; one source&rsquo;s telemetry is stale.</small>
        </span>
      </div>
      <div className={styles.mPanel}>
        <PanelHead title="Signals" />
        <ul className={styles.mSignals}>
          {signals.map((signal) => (
            <li key={signal.name}>
              <span>{signal.name}</span>
              <Chip tone={signal.tone}>{signal.state}</Chip>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

const SCREENS: Record<ScreenKey, () => ReactNode> = {
  dashboard: DashboardScreen,
  assets: AssetsScreen,
  sources: SourcesScreen,
  threat: ThreatScreen,
  alerts: AlertsScreen,
  incidents: IncidentsScreen,
  response: ResponseScreen,
  evidence: EvidenceScreen,
  health: HealthScreen,
};

/** One illustrative Guard window, with the given screen active. */
export function ProductPreview({ screen }: { screen: ScreenKey }) {
  const Screen = SCREENS[screen];
  const current = PRODUCT_SCREENS.find((item) => item.key === screen);
  return (
    <div className={styles.window}>
      <div className={styles.windowBar}>
        <span className={styles.windowDots}>
          <span />
          <span />
          <span />
        </span>
        <span className={styles.windowCrumbs}>
          <strong>RWA Guard</strong>
          <span>/</span>
          <span>{current?.label}</span>
        </span>
        <span className={styles.windowChip}>Sample workspace</span>
      </div>
      <div className={styles.windowBody}>
        <div className={styles.appNav}>
          <span className={styles.appBrand}>
            <DecodaShield className={styles.appBrandMark} strong />
            Decoda
          </span>
          <ul>
            {PRODUCT_SCREENS.map((item) => (
              <li key={item.key} data-active={item.key === screen ? 'true' : undefined}>
                <HomeIcon name={item.icon} size={14} />
                <span>{item.label}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className={styles.appMain}>
          <div className={styles.appTitle}>
            <strong>{current?.label}</strong>
            <span>Sample data</span>
          </div>
          <Screen />
        </div>
      </div>
    </div>
  );
}
