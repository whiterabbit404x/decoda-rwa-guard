'use client';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';

type OnboardingStep = { key: string; complete: boolean; source: 'manual' | 'automatic' | 'pending' };
type OnboardingState = { workspace_id: string; workspace_name: string | null; steps: OnboardingStep[]; completed_steps: number; total_steps: number; progress_percent: number; completed: boolean };

const STEP_COPY: Record<string, { label: string; detail: string; href: string }> = {
  workspace_created: { label: 'Workspace created', detail: 'Create and select your organization workspace.', href: '/workspaces' },
  industry_profile: { label: 'Industry and use-case profile', detail: 'Set your primary use case for onboarding guidance.', href: '/onboarding' },
  asset_added: { label: 'First asset or target', detail: 'Add an asset, contract, or wallet to monitor.', href: '/assets' },
  policy_configured: { label: 'Policy configured', detail: 'Save at least one threat/compliance/resilience policy.', href: '/settings' },
  integration_connected: { label: 'Integration connected', detail: 'Connect Slack or webhook notifications.', href: '/integrations' },
  teammates_invited: { label: 'Invite teammates', detail: 'Invite your team so operations are shared.', href: '/settings' },
  analysis_run: { label: 'First analysis run', detail: 'Run your first threat, compliance, or resilience analysis.', href: '/threat' },
};

export default function OnboardingPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [industry, setIndustry] = useState('asset-manager');
  const [status, setStatus] = useState<string | null>(null);

  const isComplete = useMemo(() => Boolean(state?.completed), [state?.completed]);

  async function loadState() {
    const response = await fetch(`${apiUrl}/onboarding/state`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) {
      setStatus('Unable to load onboarding progress.');
      return;
    }
    const payload = await response.json() as OnboardingState;
    setState(payload);
  }

  useEffect(() => { void loadState(); }, []);

  async function completeManualStep(step: 'industry_profile' | 'policy_configured') {
    const response = await fetch(`${apiUrl}/onboarding/state`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ step, complete: true, metadata: { industry } }),
    });
    if (!response.ok) {
      setStatus('Could not update onboarding progress.');
      return;
    }
    const payload = await response.json() as OnboardingState;
    setState(payload);
    setStatus(step === 'industry_profile' ? `Industry profile saved: ${industry}.` : 'Policy baseline confirmed.');
  }

  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Self-serve setup wizard</p><h1>Workspace onboarding</h1><p className="lede">Complete this checklist to move from signup to daily operations with no manual founder help.</p></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Progress</p><h2>{state?.completed_steps ?? 0} / {state?.total_steps ?? 7} steps complete</h2><p className="muted">Current workspace: <strong>{state?.workspace_name ?? 'No workspace selected'}</strong></p><p className="muted">Completion: {state?.progress_percent ?? 0}%</p><p className="muted">{isComplete ? 'Onboarding complete — your workspace is fully operational.' : 'Finish remaining steps to unlock a production-ready baseline.'}</p>{status ? <p className="statusLine">{status}</p> : null}</article><article className="dataCard"><p className="sectionEyebrow">Industry profile</p><select value={industry} onChange={(event) => setIndustry(event.target.value)}><option value="asset-manager">Asset manager</option><option value="stablecoin-issuer">Stablecoin issuer</option><option value="exchange">Exchange / brokerage</option><option value="custodian">Custodian</option><option value="other">Other</option></select><button type="button" onClick={() => void completeManualStep('industry_profile')}>Save industry profile</button><button type="button" onClick={() => void completeManualStep('policy_configured')}>Mark policy baseline complete</button></article><article className="dataCard"><p className="sectionEyebrow">Need help?</p><p className="muted">Use the in-app guide to onboard your workspace end-to-end.</p><Link href="/help" prefetch={false}>Open help center</Link></article></div></section><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Checklist</p><h2>Resumable first-run workflow</h2></div></div><div className="stack compactStack">{(state?.steps ?? []).map((step) => { const copy = STEP_COPY[step.key] ?? { label: step.key, detail: '', href: '/dashboard' }; return <article key={step.key} className="dataCard"><div className="listHeader"><div><h3>{step.complete ? '✓' : '○'} {copy.label}</h3><p className="muted">{copy.detail}</p></div><span className="ruleChip">{step.source}</span></div><Link href={copy.href} prefetch={false}>Go to step</Link></article>; })}</div></section></main>;
}
