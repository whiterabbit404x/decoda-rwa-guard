import type { RuntimeConfig } from './runtime-config-schema';

export type AuthFormState = {
  submitDisabled: boolean;
  statusMessage: string | null;
  deploymentWarning: string | null;
};

export function resolveAuthFormState(runtimeConfig: RuntimeConfig, configLoading: boolean, submitting: boolean): AuthFormState {
  if (configLoading) {
    return {
      submitDisabled: true,
      statusMessage: 'Loading deployment runtime configuration before enabling auth.',
      deploymentWarning: null,
    };
  }

  if (!runtimeConfig.configured) {
    return {
      submitDisabled: true,
      statusMessage: runtimeConfig.diagnostic ?? 'Auth is disabled until this deployment exposes a valid API_URL.',
      deploymentWarning: runtimeConfig.diagnostic?.includes('localhost as API base URL')
        ? 'Deployment warning: production web auth is still pointing at localhost. Update API_URL on the server/runtime and redeploy or refresh the deployment config.'
        : null,
    };
  }

  return {
    submitDisabled: submitting,
    statusMessage: null,
    deploymentWarning: runtimeConfig.diagnostic?.includes('localhost as API base URL')
      ? 'Deployment warning: production web auth is still pointing at localhost. Update API_URL on the server/runtime and redeploy or refresh the deployment config.'
      : null,
  };
}
