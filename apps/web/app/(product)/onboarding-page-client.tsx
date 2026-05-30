'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';
import { useRuntimeSummary } from '../runtime-summary-context';

import type { OnboardingProgress } from '../onboarding-progress';
import type { WorkspaceMonitoringTruth } from '../workspace-monitoring-truth';
import RuntimeSummaryPanel from '../runtime-summary-panel';
import { ActionPanel } from '../components/ui-primitives';
import { NEXT_ACTION_CTA, ONBOARDING_TOP_STEPPER, WORKFLOW_STEP_ORDER } from '../workflow-steps';

function workflowCompletionFromState(state: OnboardingProgress | null, stepId: string): boolean {
  if (!state) return false;
  const byKey = new Map(state.steps.map((step) => [step.key, step.complete]));
  switch (stepId) {
    case 'workspace_created': return true;
    case 'asset_created': return Boolean(byKey.get('asset_added'));
    case 'asset_verified': return Boolean(byKey.get('asset_added'));
    case 'monitoring_target_created': return Boolean(byKey.get('target_created'));
    case 'monitored_system_created': return Boolean(byKey.get('monitoring_started'));
    case 'worker_reporting': return Boolean(byKey.get('monitoring_started'));
    case 'telemetry_received': return Boolean(byKey.get('evidence_recorded'));
    case 'detection_created': return Boolean(byKey.get('evidence_recorded'));
    case 'alert_created': return Boolean(byKey.get('evidence_recorded'));
    case 'incident_opened': return Boolean(byKey.get('evidence_recorded'));
    case 'response_ready': return Boolean(byKey.get('evidence_recorded'));
    case 'evidence_export_ready': return Boolean(byKey.get('evidence_recorded'));
    default: return false;
  }
}

const STEP_COPY: Record<string, { title: string; detail: string; href: string; cta: string }> = {
  asset_added:       { title: 'Add your first asset',             detail: 'Register the first wallet or contract your team needs to protect.',             href: '/assets',  cta: 'Add Asset' },
  target_created:    { title: 'Create a monitoring target',       detail: 'Attach detection rules to the asset so monitoring has clear scope.',            href: '/targets', cta: 'Create Target' },
  monitoring_started:{ title: 'Start live monitoring',            detail: 'Enable at least one target so the worker continuously evaluates activity.',     href: '/targets', cta: 'Add Monitoring Source' },
  evidence_recorded: { title: 'Review first alerts and evidence', detail: 'Open threat monitoring and confirm your evidence timeline is flowing.',         href: '/threat',  cta: 'Review Evidence' },
};

const STEP_TO_NEXT_ACTION_KEY: Record<string, string> = {
  workspace_created:         'add_asset',
  asset_created:             'verify_asset',
  asset_verified:            'create_monitoring_target',
  monitoring_target_created: 'enable_monitored_system',
  monitored_system_created:  'start_simulator_signal',
  worker_reporting:          'start_simulator_signal',
  telemetry_received:        'view_detection',
  detection_created:         'open_incident',
  alert_created:             'open_incident',
  incident_opened:           'export_evidence_package',
  response_ready:            'export_evidence_package',
  evidence_export_ready:     'export_evidence_package',
};

function progressFromRuntimeSummary(summary: WorkspaceMonitoringTruth): OnboardingProgress {
  const isLive = summary.runtime_status === 'live' || summary.status_reason === 'live_runtime_verified';
  const hasAssets = isLive || summary.protected_assets_count > 0;
  const hasSystems = isLive || summary.reporting_systems_count > 0;
  const steps = [
    { key: 'asset_added' as const, complete: hasAssets, source: 'automatic' as const },
    { key: 'target_created' as const, complete: hasAssets, source: 'automatic' as const },
    { key: 'monitoring_started' as const, complete: hasSystems, source: 'automatic' as const },
    { key: 'evidence_recorded' as const, complete: isLive, source: 'automatic' as const },
  ];
  const completedSteps = steps.filter((s) => s.complete).length;
  return {
    workspace_name: summary.workspace_name,
    steps,
    completed_steps: completedSteps,
    total_steps: 4,
    progress_percent: Math.round((completedSteps / 4) * 100),
    completed: completedSteps === 4,
    next_step: steps.find((s) => !s.complete)?.key ?? null,
    counts: {
      assets: summary.protected_assets_count,
      targets: 0,
      monitoring_targets: summary.reporting_systems_count,
      evaluated_targets: 0,
      event_receipts: 0,
    },
  };
}

export default function OnboardingPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const { summary } = useRuntimeSummary();
  const [state, setState] = useState<OnboardingProgress | null>(null);
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [sessionExpired, setSessionExpired] = useState(false);

  async function loadState() {
    const response = await fetch(`${apiUrl}/onboarding/progress`, { headers: authHeaders(), cache: 'no-store' });
    if (response.status === 401) {
      setSessionExpired(true);
      return;
    }
    if (!response.ok) {
      setErrorMsg('Unable to load onboarding progress right now.');
      return;
    }
    setState(await response.json() as OnboardingProgress);
    setErrorMsg('');
    setSessionExpired(false);
  }

  useEffect(() => { void loadState(); }, []);

  // When onboarding/progress is unavailable, derive step completion from runtime-status
  // so a 401 or transient error does not render the page as OFFLINE.
  const progressUnavailable = sessionExpired || errorMsg !== '';
  const effectiveState: OnboardingProgress | null = state ?? (progressUnavailable ? progressFromRuntimeSummary(summary) : null);

  const nextStep = useMemo(() => effectiveState?.steps.find((step) => !step.complete) ?? null, [effectiveState]);
  const nextCopy = nextStep ? STEP_COPY[nextStep.key] : null;

  const topStepperSteps = ONBOARDING_TOP_STEPPER.map((step) => ({
    ...step,
    complete: workflowCompletionFromState(effectiveState, step.canonicalStepId),
  }));
  const topStepperCurrentIndex = topStepperSteps.findIndex((step) => !step.complete);
  const topStepperActiveIndex = topStepperCurrentIndex === -1 ? topStepperSteps.length - 1 : topStepperCurrentIndex;

  const workflowSteps = WORKFLOW_STEP_ORDER.map((id) => ({ id, complete: workflowCompletionFromState(effectiveState, id) }));
  const firstPendingStep = workflowSteps.find((step) => !step.complete);
  const nextActionKey = firstPendingStep
    ? (STEP_TO_NEXT_ACTION_KEY[firstPendingStep.id] ?? 'review_reason_codes')
    : null;
  const nextRequiredActionCta = nextActionKey ? (NEXT_ACTION_CTA[nextActionKey] ?? null) : null;

  return (
    <main className="productPage" data-testid="onboarding-page">
      <RuntimeSummaryPanel />

      <section className="featureSection">
        <header className="onboardingHeader">
          <h1 className="onboardingTitle">Welcome to Decoda RWA Guard</h1>
          <p className="onboardingSubtitle">Complete the setup below to start monitoring your protected assets.</p>
        </header>

        {/* Horizontal 5-step setup flow */}
        <div
          aria-label="Onboarding steps"
          data-testid="onboarding-top-stepper"
          role="list"
          className="onboardingStepper"
        >
          {topStepperSteps.map((step, index) => {
            const stepStatus = step.complete
              ? 'complete'
              : index === topStepperActiveIndex
                ? 'current'
                : 'upcoming';
            return (
              <div
                key={step.canonicalStepId}
                role="listitem"
                className="onboardingStepItem"
                data-step-status={stepStatus}
              >
                {index > 0 && (
                  <div
                    className={`stepConnector${step.complete ? ' stepConnectorComplete' : ''}`}
                    aria-hidden="true"
                  />
                )}
                <div
                  className="stepCircle"
                  data-state={stepStatus}
                  aria-current={stepStatus === 'current' ? 'step' : undefined}
                >
                  {step.complete ? (
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                      <path d="M3 8l3.5 3.5L13 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : (
                    <span>{index + 1}</span>
                  )}
                </div>
                <span className="stepLabel">{step.label}</span>
              </div>
            );
          })}
        </div>

        {/* Next Step card | Resources card */}
        <div className="onboardingCardsRow">
          <ActionPanel title="Next Step">
            <div data-testid="next-step-card">
              {sessionExpired ? (
                <p className="onboardingError" data-testid="session-expired-notice">
                  Session expired, please{' '}
                  <Link href="/sign-in" prefetch={false} style={{ color: 'var(--text-accent)' }}>sign in again</Link>
                  {' '}to continue.
                </p>
              ) : nextCopy ? (
                <>
                  <p className="onboardingStepName">{nextCopy.title}</p>
                  <p className="muted">{nextCopy.detail}</p>
                  <Link
                    href={nextCopy.href}
                    prefetch={false}
                    className="btn btn-primary onboardingCta"
                    data-testid="onboarding-cta"
                    data-next-required-action={nextActionKey ?? ''}
                  >
                    {nextRequiredActionCta ?? nextCopy.cta}
                  </Link>
                </>
              ) : (
                <>
                  <p className="muted">All core setup steps are complete.</p>
                  <div className="buttonRow">
                    <Link href="/dashboard" prefetch={false} className="btn btn-primary">Open Dashboard</Link>
                    <Link href="/threat" prefetch={false} className="btn btn-secondary">Open Threat Monitoring</Link>
                  </div>
                </>
              )}
              {errorMsg ? <p className="onboardingError">{errorMsg}</p> : null}
            </div>
          </ActionPanel>

          <ActionPanel title="Resources">
            <div data-testid="resources-card">
              <ul className="resourcesList">
                <li><Link href="/help" prefetch={false}>Documentation</Link></li>
                <li><Link href="/integrations" prefetch={false}>Integration Guide</Link></li>
                <li><Link href="/help" prefetch={false}>API Reference</Link></li>
                <li><Link href="/help" prefetch={false}>Help Center</Link></li>
              </ul>
            </div>
          </ActionPanel>
        </div>
      </section>
    </main>
  );
}
