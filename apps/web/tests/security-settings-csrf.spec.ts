import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { POST as postMfaEnroll } from '../app/api/auth/mfa/enroll/route';
import { POST as postMfaConfirm } from '../app/api/auth/mfa/confirm/route';
import { POST as postSignoutAll } from '../app/api/auth/signout-all/route';
import { safeInternalReturnTo } from '../app/safe-internal-return-to';

type FetchMock = typeof fetch;

// __dirname is apps/web/tests, so this resolves to apps/web/app regardless of the cwd the
// runner is invoked from (the repo has tests that assume both root and apps/web cwds).
const appRoot = path.join(__dirname, '..', 'app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

function withEnv(overrides: Record<string, string | undefined>, run: () => Promise<void> | void) {
  const originalValues = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(overrides)) {
    originalValues.set(key, process.env[key]);
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
  const restore = () => {
    for (const [key, value] of originalValues.entries()) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  };
  try {
    const result = run();
    if (result instanceof Promise) {
      return result.finally(restore);
    }
    restore();
    return result;
  } catch (error) {
    restore();
    throw error;
  }
}

async function withMockFetch(implementation: FetchMock, run: () => Promise<void> | void) {
  const originalFetch = global.fetch;
  global.fetch = implementation;
  try {
    await run();
  } finally {
    global.fetch = originalFetch;
  }
}

// The token shape the backend actually signs+validates: nonce.hmacsig. The cookie the
// browser presents (decoda_csrf) must equal the X-CSRF-Token header (double-submit) AND
// be relayed to the backend so its own HMAC check can pass.
const CSRF = 'a1b2c3d4e5f6.deadbeefcafebabe';

test.describe('auth proxy forwards X-CSRF-Token to the backend (Security Settings step-up fix)', () => {
  test('Enroll MFA proxy forwards the X-CSRF-Token header to the backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      let seenHeader: string | null = null;
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/auth/mfa/enroll');
        const headers = new Headers(init?.headers);
        seenHeader = headers.get('X-CSRF-Token');
        expect(headers.get('Authorization')).toBe('Bearer session-token');
        return new Response(JSON.stringify({ otpauth_uri: 'otpauth://totp/x', secret: 'S' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postMfaEnroll(new Request('http://localhost/api/auth/mfa/enroll', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer session-token',
            'X-CSRF-Token': CSRF,
            cookie: `decoda_csrf=${CSRF}`,
          },
        }));
        expect(response.status).toBe(200);
      });
      // The backend must have received the anti-CSRF token, not had it stripped by the proxy.
      expect(seenHeader).toBe(CSRF);
    });
  });

  test('Verify MFA proxy forwards the X-CSRF-Token header to the backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      let seenHeader: string | null = null;
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/auth/mfa/confirm');
        const headers = new Headers(init?.headers);
        seenHeader = headers.get('X-CSRF-Token');
        return new Response(JSON.stringify({ mfa_enabled: true, recovery_codes: ['x'] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postMfaConfirm(new Request('http://localhost/api/auth/mfa/confirm', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer session-token',
            'X-CSRF-Token': CSRF,
            'Content-Type': 'application/json',
            cookie: `decoda_csrf=${CSRF}`,
          },
          body: JSON.stringify({ code: '123456' }),
        }));
        expect(response.status).toBe(200);
      });
      expect(seenHeader).toBe(CSRF);
    });
  });

  test('Sign out all sessions proxy forwards the X-CSRF-Token header to the backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      let seenHeader: string | null = null;
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/auth/signout-all');
        const headers = new Headers(init?.headers);
        seenHeader = headers.get('X-CSRF-Token');
        return new Response(JSON.stringify({ signed_out: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postSignoutAll(new Request('http://localhost/api/auth/signout-all', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer session-token',
            'X-CSRF-Token': CSRF,
            cookie: `decoda_csrf=${CSRF}`,
          },
        }));
        expect(response.status).toBe(200);
      });
      expect(seenHeader).toBe(CSRF);
    });
  });

  test('double-submit gate rejects a cookie/header mismatch before calling the backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('backend must not be called when the CSRF double-submit check fails');
      }, async () => {
        const response = await postMfaEnroll(new Request('http://localhost/api/auth/mfa/enroll', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer session-token',
            'X-CSRF-Token': 'header-token',
            cookie: 'decoda_csrf=a-different-cookie-token',
          },
        }));
        expect(response.status).toBe(403);
        const body = await response.json();
        expect(body.code).toBe('csrf_invalid');
      });
    });
  });

  test('double-submit gate rejects a missing CSRF header before calling the backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('backend must not be called when the CSRF header is missing');
      }, async () => {
        const response = await postMfaEnroll(new Request('http://localhost/api/auth/mfa/enroll', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer session-token',
            cookie: `decoda_csrf=${CSRF}`,
          },
        }));
        expect(response.status).toBe(403);
        const body = await response.json();
        expect(body.code).toBe('csrf_invalid');
      });
    });
  });
});

test.describe('auth proxy CSRF-forwarding source contract', () => {
  test('proxyAuthRequest relays the anti-CSRF header, matching the canonical mutation proxy', () => {
    const source = read('api/auth/_shared/proxy.ts');
    expect(source).toContain("request.headers.get('x-csrf-token')");
    expect(source).toContain("headers.set('X-CSRF-Token'");
  });

  test('canonical backend proxy also forwards the anti-CSRF header (shared contract)', () => {
    const source = read('api/_shared/backend-proxy.ts');
    expect(source).toContain("request.headers.get('x-csrf-token')");
    expect(source).toContain("headers.set('X-CSRF-Token'");
  });
});

test.describe('return_to is validated as a safe internal route', () => {
  test('accepts an internal response-action path with the preserved action/incident context', () => {
    const value = '/response-actions?action_id=act-1&incident_id=inc-9&approval=yes&tab=recommended';
    expect(safeInternalReturnTo(value)).toBe(value);
  });

  test('accepts a simple internal settings path', () => {
    expect(safeInternalReturnTo('/settings/security')).toBe('/settings/security');
  });

  test('rejects empty / null values', () => {
    expect(safeInternalReturnTo(null)).toBeNull();
    expect(safeInternalReturnTo('')).toBeNull();
  });

  test('rejects protocol-relative external targets', () => {
    expect(safeInternalReturnTo('//evil.example')).toBeNull();
    expect(safeInternalReturnTo('/\\evil.example')).toBeNull();
  });

  test('rejects absolute and scheme URLs (open redirect / javascript:)', () => {
    expect(safeInternalReturnTo('https://evil.example/response-actions')).toBeNull();
    expect(safeInternalReturnTo('http://evil.example')).toBeNull();
    expect(safeInternalReturnTo('javascript:alert(1)')).toBeNull();
    expect(safeInternalReturnTo('data:text/html,x')).toBeNull();
  });

  test('rejects control characters / whitespace that could smuggle a second URL', () => {
    expect(safeInternalReturnTo('/response-actions\n//evil.example')).toBeNull();
    expect(safeInternalReturnTo('/response-actions\thttps://evil.example')).toBeNull();
  });

  test('rejects absurdly long values', () => {
    expect(safeInternalReturnTo(`/${'a'.repeat(4096)}`)).toBeNull();
  });
});

test.describe('Security Settings secure-session UX + canonical client', () => {
  const security = read('security-settings-page-client.tsx');
  const authContext = read('pilot-auth-context.tsx');

  test('auth context exposes csrfReady derived from the bootstrapped token', () => {
    expect(authContext).toContain('csrfReady: boolean');
    expect(authContext).toContain('csrfReady: Boolean(csrfToken)');
  });

  test('mutations attach credentials + CSRF via the shared authHeaders() helper', () => {
    // enroll / verify / disable go through the auth context helpers, which build
    // Authorization + X-Workspace-Id + X-CSRF-Token from authHeaders().
    expect(authContext).toContain('const enrollMfa = useCallback');
    expect(authContext).toContain("fetch('/api/auth/mfa/enroll'");
    expect(authContext).toContain('headers: authHeaders()');
    // sign out all sessions uses the same header builder.
    expect(security).toContain("fetch('/api/auth/signout-all', { method: 'POST', headers: authHeaders() })");
  });

  test('commands stay disabled until the secure session is ready (fail-closed)', () => {
    expect(security).toContain('const secureSessionReady = csrfReady;');
    expect(security).toContain('const commandsDisabled = submitting || !secureSessionReady;');
    expect(security).toContain('Preparing secure session');
    // Enroll MFA and Sign out all sessions are gated on the secure session.
    expect(security).toContain('onClick={() => void startMfaEnrollment()} disabled={commandsDisabled}');
    expect(security).toContain('onClick={() => void signOutAllSessions()} disabled={commandsDisabled}');
  });

  test('a secure-session failure is shown near the command with a Retry affordance', () => {
    expect(security).toContain('Retry secure session');
    expect(security).toContain('setSecureSessionError');
  });

  test('failures are scoped and typed: enrollment vs verification vs secure session', () => {
    // A failure under one control must never be echoed under an unrelated one.
    expect(security).toContain('MFA enrollment failed');
    expect(security).toContain('MFA verification failed');
    expect(security).toContain('setMfaError');
    expect(security).toContain('setSessionError');
    // A failed enrollment must not claim a challenge/email was delivered.
    expect(security).not.toContain('challenge sent');
    expect(security).not.toContain('email sent');
  });

  test('return_to is validated through the shared safe-internal-route helper', () => {
    expect(security).toContain("from 'app/safe-internal-return-to'");
    expect(security).toContain('safeInternalReturnTo(searchParams.get');
  });
});
