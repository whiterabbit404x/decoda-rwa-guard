'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export default function DashboardOnboardingPanel({ liveApiReachable }: { liveApiReachable: boolean }) {
  const { user, apiUrl, authHeaders } = usePilotAuth();
  const [onboardingProgress, setOnboardingProgress] = useState<{ completed_steps: number; total_steps: number; progress_percent: number } | null>(null);

  useEffect(() => {
    if (!apiUrl || !user?.current_workspace?.id) {
      setOnboardingProgress(null);
      return;
    }
    void fetch(`${apiUrl}/onboarding/state`, { headers: authHeaders(), cache: 'no-store' })
      .then(async (response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (!payload) {
          return;
        }
        setOnboardingProgress({
          completed_steps: Number(payload.completed_steps ?? 0),
          total_steps: Number(payload.total_steps ?? 7),
          progress_percent: Number(payload.progress_percent ?? 0),
        });
      })
      .catch(() => setOnboardingProgress(null));
  }, [apiUrl, authHeaders, user?.current_workspace?.id]);

  const checklist = useMemo(() => {
    const hasWorkspace = Boolean(user?.current_workspace);
    return [
      { label: 'Account created', complete: Boolean(user?.id) },
      { label: 'Workspace ready', complete: hasWorkspace },
      { label: 'Live API reachable', complete: liveApiReachable },
      { label: 'First analysis run', complete: Boolean(onboardingProgress && onboardingProgress.completed_steps > 0) },
      { label: onboardingProgress ? `Setup progress ${onboardingProgress.completed_steps}/${onboardingProgress.total_steps}` : 'Setup progress pending', complete: Boolean(onboardingProgress && onboardingProgress.completed_steps > 0) },
    ];
  }, [liveApiReachable, onboardingProgress, user?.current_workspace, user?.id]);

  return (
    <section className="dataCard">
      <div className="listHeader">
        <div>
          <p className="sectionEyebrow">Welcome</p>
          <h2>Start here</h2>
          <p className="muted">Decoda RWA Guard helps your team detect threats, apply compliance controls, and maintain operational resilience.</p>
        </div>
      </div>
      <p className="muted">Signed in as <strong>{user?.email ?? 'unknown user'}</strong> in <strong>{user?.current_workspace?.name ?? 'no workspace selected'}</strong>.</p>
      <div className="chipRow">
        {checklist.map((item) => (
          <span key={item.label} className="ruleChip">{item.complete ? '✓' : '○'} {item.label}</span>
        ))}
      </div>
      <p className="muted">{onboardingProgress ? `Workspace onboarding completion: ${onboardingProgress.progress_percent}%` : 'Load onboarding checklist to track setup completion.'}</p>
      <div className="heroActionRow">
        <Link href="/onboarding" prefetch={false}>Open setup wizard</Link>
        <Link href="/threat" prefetch={false}>Run your first threat analysis</Link>
        {!user?.current_workspace ? <Link href="/workspaces" prefetch={false}>Set up workspace</Link> : null}
      </div>
    </section>
  );
}
