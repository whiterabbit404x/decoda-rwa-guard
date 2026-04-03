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
    return 'Billing is intentionally disabled for this pilot environment. Contact sales to discuss rollout options.';
  }
  if ((runtime.provider ?? '').toLowerCase() === 'none') {
    return 'Billing is intentionally disabled for this pilot environment. Contact sales to request commercial activation.';
  }
  return runtime.message ?? 'Billing is currently unavailable. Contact support if you need plan changes.';
}
