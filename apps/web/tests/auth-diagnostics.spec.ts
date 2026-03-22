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

  test('classifies same-origin proxy transport failures without blaming Railway CORS', async () => {
    const message = classifyAuthTransportError('sign in', '/api/auth/signin', new TypeError('Failed to fetch'));

    expect(message).toContain('same-origin auth proxy');
    expect(message).toContain('/api/auth/signin');
    expect(message).not.toContain('CORS');
  });

  test('classifies invalid runtime config from the web auth proxy clearly', async () => {
    const message = classifyAuthResponseError(
      'sign in',
      '/api/auth/signin',
      500,
      'API_URL or NEXT_PUBLIC_API_URL is required in production.',
      {
        authTransport: 'same-origin proxy',
        backendApiUrl: null,
        configured: false,
        code: 'invalid_runtime_config',
      }
    );

    expect(message).toContain('same-origin proxy is reachable');
    expect(message).toContain('runtime config is invalid');
  });

  test('classifies backend-unreachable proxy responses separately from invalid credentials', async () => {
    const unreachable = classifyAuthResponseError(
      'sign in',
      '/api/auth/signin',
      502,
      'The web auth proxy could not reach the backend API at https://api.decoda.example. fetch failed',
      {
        authTransport: 'same-origin proxy',
        backendApiUrl: 'https://api.decoda.example',
        configured: true,
        code: 'backend_unreachable',
      }
    );
    const invalidCredentials = classifyAuthResponseError(
      'sign in',
      '/api/auth/signin',
      401,
      'Invalid email or password.',
      {
        authTransport: 'same-origin proxy',
        backendApiUrl: 'https://api.decoda.example',
        configured: true,
      }
    );

    expect(unreachable).toContain('could not reach https://api.decoda.example');
    expect(invalidCredentials).toBe('Invalid email or password.');
  });

  test('classifies missing AUTH_TOKEN_SECRET as backend auth misconfiguration', async () => {
    const message = classifyAuthResponseError('sign in', '/api/auth/signin', 500, 'AUTH_TOKEN_SECRET is not configured.', {
      authTransport: 'same-origin proxy',
      backendApiUrl: 'https://api.decoda.example',
      configured: true,
    });

    expect(message).toContain('backend authentication is misconfigured');
    expect(message).toContain('AUTH_TOKEN_SECRET is missing');
  });
});
