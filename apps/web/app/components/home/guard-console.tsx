'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';

import { DecodaShield } from './decoda-logo';
import { ILLUSTRATION_NOTE } from './home-data';
import { HomeIcon, type IconName } from './home-icons';
import styles from './console.module.css';

/**
 * The hero's product visualization: one SAMPLE incident moving through
 * Detect → Investigate → Review → Respond → Preserve evidence in a Guard-style
 * console.
 *
 * Honesty: every value is sample data, the figure says so on screen and in its
 * caption, and the whole interface is aria-hidden so a screen reader hears the
 * description rather than reading made-up values as facts. No metric,
 * customer, address or volume is shown, and nothing counts or ticks. The flow
 * mirrors what Guard enforces: AI and policy recommend, people authorize,
 * production execution is not automatic, and the evidence package's integrity
 * is verifiable offline.
 *
 * Motion: the server renders the COMPLETED workflow (step = last), so no-JS
 * and reduced-motion visitors read the whole story. After mount the loop
 * restarts from Detect. It pauses while off screen or in a background tab.
 * Every state is an attribute (`data-step`, `data-state`, `data-active`) read
 * by console.module.css — never a style prop, which the production CSP would
 * block — and nothing changes size between steps, so there is no layout shift.
 */

const STAGES: { key: string; label: string; icon: IconName }[] = [
  { key: 'detect', label: 'Detect', icon: 'detect' },
  { key: 'investigate', label: 'Investigate', icon: 'investigate' },
  { key: 'review', label: 'Review', icon: 'review' },
  { key: 'respond', label: 'Respond', icon: 'respond' },
  { key: 'evidence', label: 'Evidence', icon: 'evidence' },
];

const LAST = STAGES.length - 1;
const STEP_MS = 2800;
const HOLD_MS = 4600;

const SCOPE = [
  { name: 'Treasury token', kind: 'ERC-20', flagged: false },
  { name: 'Mint controller', kind: 'Admin role', flagged: true },
  { name: 'Redemption queue', kind: 'Contract', flagged: false },
  { name: 'Issuer multisig', kind: 'Safe', flagged: false },
];

const TRAIL = [
  { t: '00:00', event: 'alert.created', hash: '7c41…e09a' },
  { t: '00:42', event: 'incident.opened', hash: '19bd…44f1' },
  { t: '01:30', event: 'policy.approval_required', hash: 'a03e…6c12' },
  { t: '03:05', event: 'action.approved', hash: 'd2f8…0b7e' },
  { t: '03:12', event: 'evidence.sealed', hash: '5e9a…c3d4' },
];

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={styles.kv}>
      <span>{label}</span>
      <span>{children}</span>
    </div>
  );
}

function StagePanel({ index }: { index: number }) {
  switch (index) {
    case 0:
      return (
        <>
          <Row label="Signal">
            <code>RoleGranted · Mint controller</code>
          </Row>
          <Row label="Rule">Privileged change outside approved window</Row>
          <Row label="Severity">
            <span className={styles.sevHigh}>High</span>
          </Row>
        </>
      );
    case 1:
      return (
        <>
          <ol className={styles.timeline}>
            <li>
              <time>T+0s</time> Role granted to unrecognized operator
            </li>
            <li>
              <time>T+38s</time> Supply change queued by same operator
            </li>
            <li>
              <time>T+1m</time> Alerts correlated into one incident
            </li>
          </ol>
          <span className={styles.aiChip}>
            <HomeIcon name="spark" size={13} /> AI summary drafted · recommend-only
          </span>
        </>
      );
    case 2:
      return (
        <>
          <Row label="Policy engine">
            <span className={styles.policyChip}>
              <HomeIcon name="policy" size={13} /> Approval required
            </span>
          </Row>
          <div className={styles.approvers}>
            <span className={styles.approver}>
              <span className={styles.avatar}>SL</span> Security lead <HomeIcon name="check" size={13} />
            </span>
            <span className={styles.approver}>
              <span className={styles.avatar}>OP</span> Operations lead <HomeIcon name="check" size={13} />
            </span>
          </div>
          <Row label="Quorum">2 of 2 · step-up MFA</Row>
        </>
      );
    case 3:
      return (
        <>
          <Row label="Recommended">Revoke role via issuer multisig</Row>
          <Row label="Execution">
            <span className={styles.humanChip}>
              <HomeIcon name="approval" size={13} /> By your signers · not automatic
            </span>
          </Row>
          <Row label="Notified">Slack · Webhook · Email</Row>
        </>
      );
    default:
      return (
        <>
          <Row label="Evidence package">
            <span className={styles.sealChip}>
              <HomeIcon name="check" size={13} /> Sealed
            </span>
          </Row>
          <Row label="Manifest">
            <code>SHA-256 per artifact</code>
          </Row>
          <Row label="Integrity">
            <code>Merkle root · offline check</code>
          </Row>
        </>
      );
  }
}

function stageState(index: number, step: number): 'done' | 'active' | 'todo' {
  if (index < step) return 'done';
  return index === step ? 'active' : 'todo';
}

export function GuardConsole() {
  const [step, setStep] = useState<number>(LAST);
  const [live, setLive] = useState(false);
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node || (typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) {
      return undefined;
    }

    let visible = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let current = 0;

    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      if (!visible || document.hidden) return;
      timer = setTimeout(
        () => {
          current = current >= LAST ? 0 : current + 1;
          setStep(current);
          schedule();
        },
        current >= LAST ? HOLD_MS : STEP_MS,
      );
    };

    setStep(0);
    setLive(true);
    schedule();

    const observer =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver(([entry]) => {
            visible = Boolean(entry?.isIntersecting);
            schedule();
          });
    observer?.observe(node);
    const onVisibility = () => schedule();
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      if (timer) clearTimeout(timer);
      observer?.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  return (
    <figure ref={ref} className={styles.figure} data-step={step} data-live={live ? 'true' : undefined}>
      <figcaption className={styles.srOnly}>
        Illustration with sample data: a Decoda RWA Guard incident moving from detection through investigation,
        policy review and human-authorized response to a sealed evidence package.
      </figcaption>

      <div className={styles.frame} aria-hidden="true">
        <div className={styles.topbar}>
          <span className={styles.dots}>
            <span />
            <span />
            <span />
          </span>
          <span className={styles.crumbs}>
            <DecodaShield className={styles.crumbShield} strong />
            <strong>RWA Guard</strong>
            <span className={styles.sep}>/</span>
            <span>Incidents</span>
            <span className={styles.sep}>/</span>
            <code>INC-SAMPLE</code>
          </span>
          <span className={styles.sampleChip}>Sample incident</span>
        </div>

        <div className={styles.body}>
          <div className={styles.sidebar}>
            <p className={styles.sideLabel}>Monitored scope</p>
            <ul className={styles.scope}>
              {SCOPE.map((item) => (
                <li key={item.name} data-flagged={item.flagged ? 'true' : undefined}>
                  <span className={styles.scopeDot} />
                  <span className={styles.scopeText}>
                    <span>{item.name}</span>
                    <small>{item.kind}</small>
                  </span>
                </li>
              ))}
            </ul>
            <p className={styles.sideLabel}>Policy</p>
            <div className={styles.policyBox}>
              <span>Privileged changes</span>
              <strong>Approval required</strong>
            </div>
          </div>

          <div className={styles.main}>
            <div className={styles.alert}>
              <span className={styles.alertIcon}>
                <HomeIcon name="alert" size={16} />
              </span>
              <span className={styles.alertText}>
                <strong>Privileged role granted outside change window</strong>
                <small>Mint controller · sample incident</small>
              </span>
              <span className={styles.sevHigh}>High</span>
            </div>

            <div className={styles.stagesWrap}>
              <span className={styles.rail}>
                <span className={styles.railFill} />
              </span>
              <ol className={styles.stages}>
                {STAGES.map(({ key, label, icon }, index) => (
                  <li key={key} data-state={stageState(index, step)}>
                    <span className={styles.stageIcon}>
                      <HomeIcon name={icon} size={14} />
                    </span>
                    <span className={styles.stageLabel}>{label}</span>
                  </li>
                ))}
              </ol>
            </div>

            <div className={styles.panels}>
              {STAGES.map(({ key }, index) => (
                <div key={key} className={styles.panel} data-active={index === step ? 'true' : undefined}>
                  <StagePanel index={index} />
                </div>
              ))}
            </div>

            <div className={styles.trail}>
              <p className={styles.sideLabel}>Audit trail · hash-chained</p>
              <ol>
                {TRAIL.map((entry, index) => (
                  <li key={entry.event} data-shown={index <= step ? 'true' : undefined}>
                    <time>{entry.t}</time>
                    <span>{entry.event}</span>
                    <code>{entry.hash}</code>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      </div>

      <div className={`${styles.float} ${styles.floatPolicy}`} aria-hidden="true" data-on={step >= 2 ? 'true' : undefined}>
        <span className={styles.floatIcon}>
          <HomeIcon name="policy" size={16} />
        </span>
        <span>
          <small>Deterministic policy</small>
          <strong>Approval required · no override</strong>
        </span>
      </div>
      <div className={`${styles.float} ${styles.floatEvidence}`} aria-hidden="true" data-on={step >= LAST ? 'true' : undefined}>
        <span className={styles.floatIcon}>
          <HomeIcon name="check" size={16} />
        </span>
        <span>
          <small>Evidence package</small>
          <strong>Integrity verifiable offline</strong>
        </span>
      </div>

      <p className={styles.caption} aria-hidden="true">
        <span className={styles.captionMark} />
        {ILLUSTRATION_NOTE}
      </p>
    </figure>
  );
}
