import type { RuntimeConfig } from './runtime-config-schema';

export function shouldRedirectUnauthenticatedProductAccess(token: string | undefined, runtimeConfig: Pick<RuntimeConfig, 'liveModeEnabled'>) {
  return runtimeConfig.liveModeEnabled && !token;
}
