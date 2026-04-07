'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';

type ProgressStep = { key: string; complete: boolean; source: 'automatic' | 'pending' };
type OnboardingProgress = {
  workspace_name: string | null;
  steps: ProgressStep[];
  completed_steps: number;
  total_steps: number;
  progress_percent: number;
  completed: boolean;
  next_step: string | null;
  counts: { assets: number; targets: number; monitoring_targets: number; evaluated_targets: number; event_receipts: number };
};

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

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Self-serve setup wizard</p>
            <h1>Get your workspace live in minutes</h1>
            <p className="lede">Follow this linear setup flow to move from first login to continuous monitoring with persisted evidence.</p>
          </div>
          {nextCopy ? <Link href={nextCopy.href} prefetch={false}>{nextCopy.cta}</Link> : <Link href="/dashboard" prefetch={false}>Go to dashboard</Link>}
        </div>

        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Progress</p>
            <h2>{state?.completed_steps ?? 0} / {state?.total_steps ?? 4} complete</h2>
            <p className="muted">Workspace: <strong>{state?.workspace_name ?? 'Workspace unavailable'}</strong></p>
            <p className="muted">Completion: {state?.progress_percent ?? 0}%</p>
            {state?.completed ? <p className="statusLine">Workspace ready.</p> : <p className="muted">Resume setup anytime. Progress is derived from live workspace data.</p>}
            {status ? <p className="statusLine">{status}</p> : null}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Current live counts</p>
            <p className="muted">Assets: {state?.counts.assets ?? 0}</p>
            <p className="muted">Targets: {state?.counts.targets ?? 0}</p>
            <p className="muted">Monitoring enabled targets: {state?.counts.monitoring_targets ?? 0}</p>
            <p className="muted">Evidence receipts: {state?.counts.event_receipts ?? 0}</p>
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Resume setup</p>
            {nextCopy ? <><p className="muted">Next: <strong>{nextCopy.title}</strong></p><p className="muted">{nextCopy.detail}</p><Link href={nextCopy.href} prefetch={false}>{nextCopy.cta}</Link></> : <><p className="muted">All core setup steps are complete.</p><div className="buttonRow"><Link href="/dashboard" prefetch={false}>Open Dashboard</Link><Link href="/threat" prefetch={false}>Open Threat Monitoring</Link></div></>}
          </article>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Setup steps</p><h2>Four-step launch flow</h2></div></div>
        <div className="stack compactStack">
          {(state?.steps ?? []).map((step) => {
            const copy = STEP_COPY[step.key];
            if (!copy) return null;
            return (
              <article key={step.key} className="dataCard">
                <div className="listHeader">
                  <div>
                    <h3>{step.complete ? '✓' : '○'} {copy.title}</h3>
                    <p className="muted">{copy.detail}</p>
                  </div>
                  <span className="ruleChip">{step.source}</span>
                </div>
                <Link href={copy.href} prefetch={false}>{step.complete ? 'Review' : copy.cta}</Link>
              </article>
            );
          })}
        </div>
      </section>
    </main>
  );
}
