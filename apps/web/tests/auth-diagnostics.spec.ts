import { expect, test } from '@playwright/test';

import { classifyAuthResponseError, classifyAuthTransportError } from '../app/auth-diagnostics';
import { resolveApiConfig } from '../app/api-config';

test.describe('auth diagnostics helpers', () => {
  test('keeps missing production API URL diagnostics explicit', async () => {
    const config = resolveApiConfig({
      env: {
        NODE_ENV: 'production',
      } as NodeJS.ProcessEnv,
    });

    expect(config.apiUrl).toBeNull();
    expect(config.diagnostic).toBe('API_URL or NEXT_PUBLIC_API_URL is required in production.');
  });

  test('classifies localhost transport failures as unreachable API issues', async () => {
    const message = classifyAuthTransportError('sign in', 'http://127.0.0.1:8000', new TypeError('Failed to fetch'));

    expect(message).toContain('Cannot reach the API at http://127.0.0.1:8000.');
    expect(message).toContain('API_URL or NEXT_PUBLIC_API_URL');
  });

  test('classifies remote transport failures as CORS or network issues', async () => {
    const message = classifyAuthTransportError('create an account', 'https://api.decoda.example', new TypeError('Failed to fetch'));

    expect(message).toContain('CORS policy block');
    expect(message).toContain('https://api.decoda.example');
  });

  test('classifies missing AUTH_TOKEN_SECRET as backend auth misconfiguration', async () => {
    const message = classifyAuthResponseError('sign in', 'https://api.decoda.example', 500, 'AUTH_TOKEN_SECRET is not configured.');

    expect(message).toContain('backend authentication is misconfigured');
    expect(message).toContain('AUTH_TOKEN_SECRET is missing');
  });
});
