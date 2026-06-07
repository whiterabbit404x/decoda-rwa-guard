export type BillingRuntime = {
  provider?: string;
  available?: boolean;
  status?: string;
  message?: string;
};

export function billingEnabled(runtime: BillingRuntime | null | undefined): boolean {
  if (!runtime) {
    return false;
  }
  return Boolean(runtime.available) && (runtime.provider ?? '').toLowerCase() !== 'none';
}

export function billingDisabledMessage(runtime: BillingRuntime | null | undefined): string {
  if (!runtime) {
    return 'Billing is not configured for this environment. Configure a supported provider to enable self-serve plan changes.';
  }
  if ((runtime.provider ?? '').toLowerCase() === 'none') {
    return 'Billing is disabled for this environment. Configure Paddle or Stripe to enable self-serve plan changes.';
  }
  return runtime.message ?? 'Billing is currently unavailable. Contact support if you need plan changes.';
}

export function billingProviderLabel(runtime: BillingRuntime | null | undefined): string {
  const provider = (runtime?.provider ?? '').toLowerCase();
  if (provider === 'paddle') return runtime?.available ? 'Paddle configured' : 'Paddle not configured';
  if (provider === 'stripe') return runtime?.available ? 'Stripe configured' : 'Stripe not configured';
  return 'Not configured';
}
