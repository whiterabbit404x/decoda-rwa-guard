import { expect, test } from '@playwright/test';

import { classifyApiTransportError, classifyAuthResponseError, classifyAuthTransportError } from '../app/auth-diagnostics';
import { resolveApiConfig } from '../app/api-config';

test.describe('auth diagnostics helpers', () => {
  test('keeps missing production API URL diagnostics explicit', async () => {
    const config = resolveApiConfig({
      env: {
        NODE_ENV: 'production',
      } as NodeJS.ProcessEnv,
    });

    expect(config.apiUrl).toBeNull();
    expect(config.diagnostic).toBe('API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.');
  });

  test('classifies localhost transport failures as unreachable API issues', async () => {
    const message = classifyAuthTransportError('sign in', 'http://127.0.0.1:8000', new TypeError('Failed to fetch'));

    expect(message).toContain('Cannot reach the API at http://127.0.0.1:8000.');
    expect(message).toContain('API_URL or NEXT_PUBLIC_API_URL');
  });

  test('classifies missing API base URL for non-auth API requests clearly', async () => {
    const message = classifyApiTransportError('create this asset', '', new TypeError('Failed to fetch'));

    expect(message).toContain('API_URL / NEXT_PUBLIC_API_URL is missing or empty');
  });

  test('classifies generic cross-origin API transport failures with CORS hint', async () => {
    const message = classifyApiTransportError('create this asset', 'https://api.decoda.example', new TypeError('Failed to fetch'));

    expect(message).toContain('could not reach https://api.decoda.example');
    expect(message).toContain('often CORS');
  });

  test('classifies API timeout failures distinctly from generic CORS/network failures', async () => {
    const message = classifyApiTransportError('create this asset', 'https://api.decoda.example', new Error('Request timed out after 5000ms'));

    expect(message).toContain('timed out');
    expect(message).toContain('https://api.decoda.example');
    expect(message).not.toContain('often CORS');
  });

  test('classifies API DNS failures as host resolution issues', async () => {
    const message = classifyApiTransportError('create this asset', 'https://api.decoda.example', new Error('getaddrinfo ENOTFOUND api.decoda.example'));

    expect(message).toContain('could not be resolved');
    expect(message).toContain('API_URL / NEXT_PUBLIC_API_URL');
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
      'API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.',
      {
        authTransport: 'same-origin proxy',
        backendApiUrl: null,
        configured: false,
        code: 'invalid_runtime_config',
      }
    );

    expect(message).toContain('API_URL or NEXT_PUBLIC_API_URL is required.');
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

    expect(unreachable).toContain('could not reach the backend API at https://api.decoda.example');
    expect(invalidCredentials).toBe('Invalid email or password.');
  });

  test('classifies missing AUTH_TOKEN_SECRET as backend auth misconfiguration', async () => {
    const message = classifyAuthResponseError('sign in', '/api/auth/signin', 500, 'AUTH_TOKEN_SECRET is not configured.', {
      authTransport: 'same-origin proxy',
      backendApiUrl: 'https://api.decoda.example',
      configured: true,
    });

    expect(message).toContain('Authentication is temporarily unavailable');
  });

  test('classifies structured auth DB outage payload as temporary backend unavailability, not invalid credentials', async () => {
    const message = classifyAuthResponseError('sign in', '/api/auth/signin', 503, 'Authentication is temporarily unavailable. Please retry in a moment.', {
      authTransport: 'same-origin proxy',
      backendApiUrl: 'https://api.decoda.example',
      configured: true,
      code: 'AUTH_DB_QUOTA_EXCEEDED',
    });

    expect(message).toContain('temporarily unavailable');
    expect(message).not.toContain('Invalid email or password');
  });
});
