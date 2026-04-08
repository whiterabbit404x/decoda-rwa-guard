export type OnboardingProgressStepKey = 'asset_added' | 'target_created' | 'monitoring_started' | 'evidence_recorded';

export type OnboardingProgressStep = {
  key: OnboardingProgressStepKey;
  complete: boolean;
  source: 'automatic' | 'pending';
};

export type OnboardingProgress = {
  workspace_name: string | null;
  steps: OnboardingProgressStep[];
  completed_steps: number;
  total_steps: number;
  progress_percent: number;
  completed: boolean;
  next_step: OnboardingProgressStepKey | null;
  counts: {
    assets: number;
    targets: number;
    monitoring_targets: number;
    evaluated_targets: number;
    event_receipts: number;
  };
};
