'use client';

import Link from 'next/link';
import { useMemo } from 'react';

import { usePilotAuth } from './pilot-auth-context';

export default function DashboardOnboardingPanel({ liveApiReachable }: { liveApiReachable: boolean }) {
  const { user } = usePilotAuth();

  const checklist = useMemo(() => {
    const hasWorkspace = Boolean(user?.current_workspace);
    const firstRunAt = typeof window !== 'undefined' ? window.localStorage.getItem('decoda-first-analysis-run-at') : null;
    return [
      { label: 'Account created', complete: Boolean(user?.id) },
      { label: 'Workspace ready', complete: hasWorkspace },
      { label: 'Live API reachable', complete: liveApiReachable },
      { label: 'First analysis run', complete: Boolean(firstRunAt) },
    ];
  }, [liveApiReachable, user?.current_workspace, user?.id]);

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
      <div className="heroActionRow">
        <Link href="/threat">Run your first threat analysis</Link>
        {!user?.current_workspace ? <Link href="/workspaces">Set up workspace</Link> : null}
      </div>
    </section>
  );
}
