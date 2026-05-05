'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import type { OnboardingProgress } from './onboarding-progress';
import { WORKFLOW_STEP_LABELS, WORKFLOW_STEP_ORDER } from './workflow-steps';

export default function DashboardOnboardingPanel({ liveApiReachable }: { liveApiReachable: boolean }) {
  const { user, apiUrl, authHeaders } = usePilotAuth();
  const [onboardingProgress, setOnboardingProgress] = useState<OnboardingProgress | null>(null);

  useEffect(() => {
    if (!apiUrl || !user?.current_workspace?.id) {
      setOnboardingProgress(null);
      return;
    }
    void fetch(`${apiUrl}/onboarding/progress`, { headers: authHeaders(), cache: 'no-store' })
      .then(async (response) => response.ok ? response.json() : null)
      .then((payload) => payload ? setOnboardingProgress(payload as OnboardingProgress) : null)
      .catch(() => setOnboardingProgress(null));
  }, [apiUrl, authHeaders, user?.current_workspace?.id]);

  const workspaceName = user?.current_workspace?.name ?? (user?.memberships?.[0]?.workspace.name ?? 'Workspace unavailable');

  const checklist = useMemo(() => {
    const steps = onboardingProgress?.steps ?? [];
    const stepMap = new Map(steps.map((step) => [step.key, step.complete]));
    return [
      { label: 'Step 1: Add asset', complete: Boolean(stepMap.get('asset_added')) },
      { label: 'Step 2: Create target', complete: Boolean(stepMap.get('target_created')) },
      { label: 'Step 3: Start monitoring', complete: Boolean(stepMap.get('monitoring_started')) },
      { label: 'Step 4: Review first evidence', complete: Boolean(stepMap.get('evidence_recorded')) },
      { label: onboardingProgress ? `Progress ${onboardingProgress.completed_steps}/${onboardingProgress.total_steps}` : 'Progress pending', complete: Boolean(onboardingProgress?.completed_steps) },
      { label: 'Live API reachable', complete: liveApiReachable },
    ];
  }, [liveApiReachable, onboardingProgress]);

  const compactWorkflow = WORKFLOW_STEP_ORDER.map((stepId, index) => `${index + 1}. ${WORKFLOW_STEP_LABELS[stepId]}`).join(' · ');

  return (
    <section className="dataCard">
      <div className="listHeader">
        <div>
          <p className="sectionEyebrow">Welcome</p>
          <h2>Start here</h2>
          <p className="muted">Complete the four-step launch flow to move from setup to evidence-backed monitoring.</p>
        </div>
      </div>
      <p className="muted">Signed in as <strong>{user?.email ?? 'unknown user'}</strong> in <strong>{workspaceName}</strong>.</p>
      <div className="chipRow">
        {checklist.map((item) => (
          <span key={item.label} className="ruleChip">{item.complete ? '✓' : '○'} {item.label}</span>
        ))}
      </div>
      <p className="muted">{onboardingProgress ? `Workspace onboarding completion: ${onboardingProgress.progress_percent}%` : 'Load onboarding checklist to track setup completion.'}</p>
      <p className="muted"><strong>Workflow:</strong> {compactWorkflow}</p>
      <div className="heroActionRow">
        <Link href="/onboarding" prefetch={false}>Open setup wizard</Link>
        <Link href="/threat" prefetch={false}>Review first evidence</Link>
      </div>
    </section>
  );
}
