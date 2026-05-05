'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';

import type { OnboardingProgress } from '../onboarding-progress';
import RuntimeSummaryPanel from '../runtime-summary-panel';
import { ActionPanel, MetricCard, StepRail } from '../components/ui-primitives';
import { NEXT_ACTION_CTA, ONBOARDING_TOP_STEPPER, WORKFLOW_STEP_LABELS, WORKFLOW_STEP_ORDER } from '../workflow-steps';


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
  asset_added: { title: 'Step 1: Add your first asset', detail: 'Register the first wallet or contract your team needs to protect.', href: '/assets', cta: 'Add asset' },
  target_created: { title: 'Step 2: Create a monitoring target', detail: 'Attach detection rules to the asset so monitoring has clear scope.', href: '/targets', cta: 'Create target' },
  monitoring_started: { title: 'Step 3: Start live monitoring', detail: 'Enable at least one target so the worker continuously evaluates activity.', href: '/targets', cta: 'Enable monitoring' },
  evidence_recorded: { title: 'Step 4: Review first alerts and evidence', detail: 'Open threat monitoring and confirm your evidence timeline is flowing.', href: '/threat', cta: 'Review evidence' },
};

export default function OnboardingPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [state, setState] = useState<OnboardingProgress | null>(null);
  const [status, setStatus] = useState<string>('');

  async function loadState() {
    const response = await fetch(`${apiUrl}/onboarding/progress`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) {
      setStatus('Unable to load onboarding progress right now.');
      return;
    }
    setState(await response.json() as OnboardingProgress);
    setStatus('');
  }

  useEffect(() => { void loadState(); }, []);

  const nextStep = useMemo(() => state?.steps.find((step) => !step.complete) ?? null, [state]);
  const nextCopy = nextStep ? STEP_COPY[nextStep.key] : null;
  const workflowSteps = WORKFLOW_STEP_ORDER.map((id) => ({ id, complete: workflowCompletionFromState(state, id) }));
  const firstPendingStep = workflowSteps.find((step) => !step.complete);
  const topStepperSteps = ONBOARDING_TOP_STEPPER.map((step) => ({
    ...step,
    complete: workflowCompletionFromState(state, step.canonicalStepId),
  }));
  const topStepperCurrentIndex = topStepperSteps.findIndex((step) => !step.complete);
  const topStepperActiveIndex = topStepperCurrentIndex === -1 ? topStepperSteps.length - 1 : topStepperCurrentIndex;
  const firstPendingCta = firstPendingStep ? NEXT_ACTION_CTA[({ workspace_created: 'add_asset', asset_created: 'verify_asset', asset_verified: 'create_monitoring_target', monitoring_target_created: 'enable_monitored_system', monitored_system_created: 'start_simulator_signal', worker_reporting: 'start_simulator_signal', telemetry_received: 'view_detection', detection_created: 'open_incident', alert_created: 'open_incident', incident_opened: 'export_evidence_package', response_ready: 'export_evidence_package', evidence_export_ready: 'export_evidence_package' } as Record<string, string>)[firstPendingStep.id] ?? 'review_reason_codes'] : null;

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="featureSection">
        <div aria-label="Onboarding steps" data-testid="onboarding-top-stepper" role="list" className="buttonRow">
          {topStepperSteps.map((step, index) => {
            const status = step.complete ? 'complete' : index === topStepperActiveIndex ? 'current' : 'upcoming';
            return (
              <span key={step.canonicalStepId} role="listitem" className="badge" data-step-status={status}>
                {step.label}
              </span>
            );
          })}
        </div>
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Self-serve setup wizard</p>
            <h1>Get your workspace live in minutes</h1>
            <p className="lede">Follow this linear setup flow to move from first login to continuous monitoring with persisted evidence.</p>
          </div>
          {nextCopy ? <Link href={nextCopy.href} prefetch={false}>{nextCopy.cta}</Link> : <Link href="/dashboard" prefetch={false}>Go to dashboard</Link>}
        </div>

        <div className="threeColumnSection">
          <MetricCard label="Progress" value={<>{state?.completed_steps ?? 0} / {state?.total_steps ?? 4} complete</>} meta={<><span>Workspace: <strong>{state?.workspace_name ?? 'Workspace unavailable'}</strong></span><br /><span>Completion: {state?.progress_percent ?? 0}%</span></>} />
          <MetricCard label="Current live counts" value={`Assets: ${state?.counts.assets ?? 0}`} meta={`Targets: ${state?.counts.targets ?? 0} · Monitoring enabled targets: ${state?.counts.monitoring_targets ?? 0} · Evidence receipts: ${state?.counts.event_receipts ?? 0}`} />
          <ActionPanel title="Resume setup">
            <p className="sectionEyebrow">Resume setup</p>
            {nextCopy ? <><p className="muted">Next: <strong>{nextCopy.title}</strong></p><p className="muted">{nextCopy.detail}</p><Link href={nextCopy.href} prefetch={false}>{nextCopy.cta}</Link></> : <><p className="muted">All core setup steps are complete.</p><div className="buttonRow"><Link href="/dashboard" prefetch={false}>Open Dashboard</Link><Link href="/threat" prefetch={false}>Open Threat Monitoring</Link></div></>}
            <p className="muted">First non-complete workflow CTA: <strong>{firstPendingCta ?? 'All steps complete'}</strong></p>
          </ActionPanel>
          <ActionPanel title="Current Step">
            {nextCopy ? <><p className="muted"><strong>{nextCopy.title}</strong></p><p className="muted">{nextCopy.detail}</p></> : <p className="muted">All backend onboarding steps currently report complete.</p>}
          </ActionPanel>
          <ActionPanel title="Resources">
            <p className="muted">Runtime truth stays backend-driven so your team can resume this setup from any session.</p>
            <ul>
              <li><Link href="/help" prefetch={false}>Read onboarding help</Link></li>
              <li><Link href="/monitoring-sources" prefetch={false}>Review monitoring sources</Link></li>
            </ul>
          </ActionPanel>
          <ActionPanel title="Next Action">
            {nextCopy ? <><p className="muted">Take the next backend-confirmed setup step.</p><Link href={nextCopy.href} prefetch={false}>{nextCopy.cta}</Link></> : <p className="muted">No pending onboarding step reported by backend progress.</p>}
          </ActionPanel>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Setup steps</p><h2>Workflow progression</h2></div></div>
        <StepRail steps={workflowSteps.map((step) => ({ key: step.id, title: WORKFLOW_STEP_LABELS[step.id], detail: WORKFLOW_STEP_LABELS[step.id], complete: step.complete, source: step.complete ? 'automatic' : 'pending', href: '/threat', cta: firstPendingCta ?? 'Review workflow' })) as any} />
      </section>
    </main>
  );
}
