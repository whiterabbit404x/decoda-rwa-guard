import { expect, test } from '@playwright/test';
import { readdirSync, readFileSync } from 'node:fs';

import { GET as backendGet, POST as backendPost } from '../app/api/backend/[...path]/route';
import { POST as signInRoute } from '../app/api/auth/signin/route';
import { GET as runtimeConfigRoute } from '../app/api/runtime-config/route';
import { BROWSER_API_BASE, browserApiUrl, isUsableClientApiBase } from '../app/api-config';
import { classifyApiTransportError } from '../app/auth-diagnostics';
import { decodaLinks, decodaSignInEnabled, guardIdentityMode, legacyPasswordsAllowed, productEntryUrl } from '../app/decoda-identity';
import { accessReasonFor, decodaReauthenticationPath, followDecodaRefusal, safeNextPath } from '../app/decoda-identity-shared';
import { attachApiCredentials, stripBrowserCredentials } from '../app/server-credentials';

const API = 'https://api.guard.test';
const SECRET = 's'.repeat(40);
const WORKOS_ENV = {
  WORKOS_CLIENT_ID: 'client_01GUARDWEB',
  WORKOS_API_KEY: 'sk_test_guard',
  WORKOS_COOKIE_PASSWORD: 'c'.repeat(40),
  NEXT_PUBLIC_WORKOS_REDIRECT_URI: 'https://rwa.decodasecurity.test/auth/callback',
};

async function withEnv<T>(overrides: Record<string, string | undefined>, run: () => Promise<T> | T): Promise<T> {
  const saved = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(overrides)) {
    saved.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  try {
    return await run();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

type Captured = { url: string; init: RequestInit };

async function withBackend<T>(respond: (url: string, init: RequestInit) => Response, run: (calls: Captured[]) => Promise<T>): Promise<T> {
  const original = global.fetch;
  const calls: Captured[] = [];
  global.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    calls.push({ url, init: init ?? {} });
    return respond(url, init ?? {});
  }) as typeof fetch;
  try {
    return await run(calls);
  } finally {
    global.fetch = original;
  }
}

const legacyEnv = { GUARD_IDENTITY_MODE: undefined, API_URL: API };

test.describe('identity modes (server-decided)', () => {
  test('legacy is the default and an unknown mode fails closed', async () => {
    await withEnv({ GUARD_IDENTITY_MODE: undefined }, () => expect(guardIdentityMode()).toBe('legacy'));
    await withEnv({ GUARD_IDENTITY_MODE: 'demo' }, () => {
      expect(guardIdentityMode()).toBe('workos');
      expect(legacyPasswordsAllowed()).toBe(false);
    });
  });

  test('passwords end at the sunset in dual mode and never exist in workos mode', async () => {
    await withEnv({ GUARD_IDENTITY_MODE: 'dual', GUARD_LEGACY_PASSWORD_SUNSET: '2030-01-01T00:00:00Z' }, () => {
      expect(legacyPasswordsAllowed(new Date('2029-12-31T00:00:00Z'))).toBe(true);
      expect(legacyPasswordsAllowed(new Date('2030-01-01T00:00:00Z'))).toBe(false);
    });
    await withEnv({ GUARD_IDENTITY_MODE: 'dual', GUARD_LEGACY_PASSWORD_SUNSET: 'soon' }, () => expect(legacyPasswordsAllowed()).toBe(false));
    await withEnv({ GUARD_IDENTITY_MODE: 'workos' }, () => expect(legacyPasswordsAllowed()).toBe(false));
  });

  test('Decoda sign-in needs the complete AuthKit configuration', async () => {
    await withEnv({ GUARD_IDENTITY_MODE: 'workos', ...WORKOS_ENV }, () => expect(decodaSignInEnabled()).toBe(true));
    await withEnv({ GUARD_IDENTITY_MODE: 'workos', ...WORKOS_ENV, WORKOS_COOKIE_PASSWORD: 'short' }, () => expect(decodaSignInEnabled()).toBe(false));
    await withEnv({ GUARD_IDENTITY_MODE: 'legacy', ...WORKOS_ENV }, () => expect(decodaSignInEnabled()).toBe(false));
  });
});

test.describe('product switcher destinations (server-configured)', () => {
  test('Vault opens at its sign-in entry, not its home page', async () => {
    await withEnv({ DECODA_WEBSITE_URL: undefined, DECODA_VAULT_URL: undefined }, () => {
      const links = decodaLinks();
      expect(links.vault).toBe('https://vault.decodasecurity.com/auth/sign-in');
      expect(links.launcher).toBe('https://www.decodasecurity.com/launcher');
      expect(links.requestAccess).toBe('https://www.decodasecurity.com/request-pilot?product=rwa_guard');
    });
    await withEnv({ DECODA_VAULT_URL: 'https://vault-staging.decoda.test/' }, () => {
      expect(decodaLinks().vault).toBe('https://vault-staging.decoda.test/auth/sign-in');
    });
    expect(productEntryUrl('https://vault.decoda.test//')).toBe('https://vault.decoda.test/auth/sign-in');
  });

  test('a non-https destination is refused in production', async () => {
    await withEnv({ NODE_ENV: 'production', DECODA_VAULT_URL: 'http://vault.decoda.test', DECODA_WEBSITE_URL: 'javascript:alert(1)' }, () => {
      const links = decodaLinks();
      expect(links.vault).toBe('https://vault.decodasecurity.com/auth/sign-in');
      expect(links.website).toBe('https://www.decodasecurity.com');
    });
  });
});

test.describe('browser-safe helpers', () => {
  test('only same-site paths are followed after sign-in', () => {
    expect(safeNextPath('/alerts?id=1')).toBe('/alerts?id=1');
    for (const unsafe of ['https://evil.test', '//evil.test', '/\\evil.test', '/api/backend/users', '/auth/session', 'javascript:alert(1)', '', null]) {
      expect(safeNextPath(unsafe as string | null)).toBe('/dashboard');
    }
  });

  test('API refusals map to honest screens', () => {
    expect(accessReasonFor('PRODUCT_ACCESS_DENIED', 'entitlement_suspended')).toBe('entitlement_suspended');
    expect(accessReasonFor('PRODUCT_ACCESS_DENIED', 'nonsense')).toBe('not_entitled');
    expect(accessReasonFor('IDENTITY_LINK_CONFLICT')).toBe('link_conflict');
    expect(accessReasonFor('SOMETHING_NEW')).toBe('unavailable');
    expect(decodaReauthenticationPath('/response-actions?id=7')).toBe('/auth/sign-in?reauth=1&next=%2Fresponse-actions%3Fid%3D7');
  });

  test('the browser is only ever given the same-origin proxy as its API base', async () => {
    expect(browserApiUrl('https://api.guard.test')).toBe(BROWSER_API_BASE);
    expect(browserApiUrl('')).toBeNull();
    await withEnv({ API_URL: API }, async () => {
      const body = await (await runtimeConfigRoute()).json();
      expect(body.apiUrl).toBe('/api/backend');
      expect(JSON.stringify(body)).not.toContain('api.guard.test');
    });
  });

  test('client components accept the same-origin proxy as a configured API base', () => {
    // Regression: the Assets page rejected /api/backend as "API endpoint is not configured".
    expect(isUsableClientApiBase(BROWSER_API_BASE)).toBe(true);
    expect(isUsableClientApiBase(`${BROWSER_API_BASE}/`)).toBe(true);
    expect(isUsableClientApiBase('https://api.guard.test')).toBe(true);
    for (const unusable of ['', '   ', null, undefined, '/api', 'api/backend', 'javascript:alert(1)']) {
      expect(isUsableClientApiBase(unusable as string | null | undefined)).toBe(false);
    }
  });

  test('a transport failure through the proxy blames the connection, not the API URL', () => {
    const offline = classifyApiTransportError('load protected assets', BROWSER_API_BASE, new TypeError('Failed to fetch'));
    expect(offline).toContain('could not reach this site (/api/backend)');
    expect(offline).not.toContain('invalid');
    expect(offline).not.toContain('CORS');
    expect(classifyApiTransportError('load protected assets', BROWSER_API_BASE, new Error('Request timed out'))).toContain('timed out');
  });

  test('a Decoda platform refusal goes to /access and keeps the session; anything else is left to the page', async () => {
    const visited: string[] = [];
    const g = globalThis as unknown as { window?: unknown };
    const savedWindow = g.window;
    g.window = { location: { assign: (path: string) => visited.push(path) } };
    try {
      const refusal = (status: number, detail: unknown) => new Response(JSON.stringify({ detail }), { status, headers: { 'content-type': 'application/json' } });
      const withdrawn = refusal(403, { code: 'PRODUCT_ACCESS_DENIED', message: 'x', reason: 'entitlement_suspended' });
      expect(await followDecodaRefusal(withdrawn)).toBe(true);
      expect(await withdrawn.json()).toHaveProperty('detail.code', 'PRODUCT_ACCESS_DENIED'); // body still readable by the page
      expect(await followDecodaRefusal(refusal(503, { code: 'IDENTITY_DIRECTORY_UNAVAILABLE', message: 'x' }))).toBe(true);
      expect(visited).toEqual(['/access?reason=entitlement_suspended', '/access?reason=unavailable']);
      // A missing session, a permission error, a CSRF failure or a legacy refusal: the page decides.
      expect(await followDecodaRefusal(refusal(401, 'Your session ended.'))).toBe(false);
      expect(await followDecodaRefusal(refusal(403, 'You do not belong to that workspace.'))).toBe(false);
      expect(await followDecodaRefusal(refusal(403, { code: 'MFA_ENROLLMENT_REQUIRED' }))).toBe(false);
      expect(await followDecodaRefusal(new Response('not json', { status: 403 }))).toBe(false);
      expect(visited).toHaveLength(2);
    } finally {
      g.window = savedWindow;
    }
  });
});

test.describe('client components never need the session token', () => {
  // Regression: after the token moved into the HttpOnly cookie, pages that
  // checked `headers.Authorization` (or required an absolute API URL) showed
  // "session missing or expired" / "API endpoint is not configured" to a
  // signed-in user. The server attaches the session; the browser never has it.
  const clientSources = (): Array<{ file: string; source: string }> => {
    const found: Array<{ file: string; source: string }> = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = `${dir}/${entry.name}`;
        if (entry.isDirectory()) {
          if (path.endsWith('/app/api')) continue; // server route handlers
          walk(path);
        } else if (/\.(ts|tsx)$/.test(entry.name)) {
          const source = readFileSync(path, 'utf8');
          // The directive may follow leading comments.
          const code = source.replace(/^(?:\s*(?:\/\/[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/))*\s*/, '');
          if (/^['"]use client['"]/.test(code)) found.push({ file: path, source });
        }
      }
    };
    walk(`${__dirname}/../app`);
    return found;
  };

  test('no client component reads, requires or sends a bearer token', () => {
    const sources = clientSources();
    expect(sources.length).toBeGreaterThan(50);
    const offenders = sources
      .filter(({ source }) => /headers\.Authorization|\[['"]Authorization['"]\]|Authorization:\s*`?Bearer|access_token|decoda_access_token/.test(source))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  test('no client component requires an absolute API URL', () => {
    const offenders = clientSources()
      .filter(({ source }) => /isValidApiBaseUrl\(|apiUrl\.startsWith\(\s*['"]http|new URL\(\s*`?\$?\{?apiUrl/.test(source))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });
});

test.describe('proxy.ts attaches credentials server-side', () => {
  test('whatever a browser sends under the credential names is removed', () => {
    const headers = new Headers({
      authorization: 'Bearer browser-supplied',
      'x-guard-proxy-secret': 'forged',
      'x-guard-identity-session': 'session_FORGED',
      'x-workspace-id': 'kept',
    });
    stripBrowserCredentials(headers);
    expect(headers.get('authorization')).toBeNull();
    expect(headers.get('x-guard-proxy-secret')).toBeNull();
    expect(headers.get('x-guard-identity-session')).toBeNull();
    expect(headers.get('x-workspace-id')).toBe('kept');
  });

  test('the session comes from the HttpOnly cookie; the binding from the live AuthKit session', () => {
    const headers = new Headers();
    attachApiCredentials(headers, { sessionToken: 'server-token', secret: SECRET });
    attachApiCredentials(headers, { identitySessionId: 'session_01LIVE' });
    expect(headers.get('authorization')).toBe('Bearer server-token');
    expect(headers.get('x-guard-proxy-secret')).toBe(SECRET);
    expect(headers.get('x-guard-identity-session')).toBe('session_01LIVE');
    const none = new Headers();
    attachApiCredentials(none, { sessionToken: null, secret: '', identitySessionId: null });
    expect([...none.keys()]).toEqual([]);
  });

  test('proxy.ts strips first, then attaches only on API requests', () => {
    const source = readFileSync('apps/web/proxy.ts', 'utf8');
    const strip = source.indexOf('stripBrowserCredentials(requestHeaders);');
    const attach = source.indexOf('attachApiCredentials(requestHeaders, { sessionToken: request.cookies.get(SESSION_COOKIE)?.value');
    expect(strip).toBeGreaterThan(-1);
    expect(attach).toBeGreaterThan(strip);
    expect(source).toContain("'/api/:path*'");
  });
});

test.describe('/api/backend/* same-origin proxy', () => {
  const context = (...path: string[]) => ({ params: Promise.resolve({ path }) });

  test('forwards the server-attached credentials and streams the answer back', async () => {
    await withEnv(legacyEnv, () => withBackend(
      () => new Response('{"items":[]}', { status: 200, headers: { 'content-type': 'application/json', 'set-cookie': 'leak=1', 'x-decoda-error-code': 'NONE' } }),
      async (calls) => {
        const response = await backendGet(new Request('http://localhost:3000/api/backend/alerts?limit=5', {
          headers: {
            authorization: 'Bearer server-token',
            'x-guard-proxy-secret': SECRET,
            'x-guard-identity-session': 'session_01LIVE',
            'x-workspace-id': '11111111-2222-4333-8444-555555555555',
            'x-csrf-token': 'csrf-1',
            cookie: 'decoda_session=server-token',
          },
        }), context('alerts'));
        expect(response.status).toBe(200);
        expect(await response.json()).toEqual({ items: [] });
        expect(response.headers.get('set-cookie')).toBeNull();
        expect(response.headers.get('x-decoda-error-code')).toBe('NONE');
        expect(calls).toHaveLength(1);
        expect(calls[0]!.url).toBe(`${API}/alerts?limit=5`);
        const sent = new Headers(calls[0]!.init.headers);
        expect(sent.get('authorization')).toBe('Bearer server-token');
        expect(sent.get('x-guard-proxy-secret')).toBe(SECRET);
        expect(sent.get('x-guard-identity-session')).toBe('session_01LIVE');
        expect(sent.get('x-workspace-id')).toBe('11111111-2222-4333-8444-555555555555');
        expect(sent.get('x-csrf-token')).toBe('csrf-1');
        expect(sent.get('cookie')).toBeNull();
      },
    ));
  });

  test('session-issuing, escaping and cross-origin requests are refused before the API is called', async () => {
    await withEnv(legacyEnv, () => withBackend(() => new Response('{}'), async (calls) => {
      expect((await backendPost(new Request('http://localhost:3000/api/backend/auth/signin', {
        method: 'POST', headers: { origin: 'http://localhost:3000', host: 'localhost:3000' }, body: '{}',
      }), context('auth', 'signin'))).status).toBe(404);
      expect((await backendGet(new Request('http://localhost:3000/api/backend/x'), context('..', 'admin'))).status).toBe(404);
      expect((await backendPost(new Request('http://localhost:3000/api/backend/alerts', {
        method: 'POST', headers: { origin: 'https://evil.test', host: 'localhost:3000' }, body: '{}',
      }), context('alerts'))).status).toBe(403);
      expect(calls).toHaveLength(0);
    }));
  });

  test('same-origin writes are forwarded with their body', async () => {
    await withEnv(legacyEnv, () => withBackend(() => new Response('{"ok":true}', { status: 201, headers: { 'content-type': 'application/json' } }), async (calls) => {
      const response = await backendPost(new Request('http://localhost:3000/api/backend/response/actions', {
        method: 'POST',
        headers: { origin: 'http://localhost:3000', host: 'localhost:3000', 'content-type': 'application/json', authorization: 'Bearer server-token' },
        body: JSON.stringify({ action: 'freeze' }),
      }), context('response', 'actions'));
      expect(response.status).toBe(201);
      expect(calls[0]!.init.method).toBe('POST');
      expect(await new Response(calls[0]!.init.body as BodyInit).text()).toBe('{"action":"freeze"}');
    }));
  });
});

test.describe('sign-in keeps the token server-side', () => {
  test('the session is set HttpOnly, the readable copy is cleared, and the body carries no token', async () => {
    await withEnv({ API_URL: API }, () => withBackend(
      () => new Response(JSON.stringify({ access_token: 'server-issued-token', token_type: 'bearer', user: { id: 'u1', email: 'a@b.test' } }), {
        status: 200, headers: { 'content-type': 'application/json' },
      }),
      async () => {
        const response = await signInRoute(new Request('http://localhost:3000/api/auth/signin', {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: 'a@b.test', password: 'x' }),
        }));
        const body = await response.json();
        expect(body.access_token).toBeUndefined();
        expect(body.user.id).toBe('u1');
        const cookies = (response.headers as Headers & { getSetCookie(): string[] }).getSetCookie();
        const session = cookies.find((c) => c.startsWith('decoda_session='));
        expect(session).toContain('server-issued-token');
        expect(session!.toLowerCase()).toContain('httponly');
        const retired = cookies.filter((c) => c.startsWith('decoda_access_token='));
        expect(retired.length).toBeGreaterThan(0);
        for (const cookie of retired) {
          expect(cookie).not.toContain('server-issued-token');
          expect(cookie.toLowerCase()).toContain('max-age=0');
        }
      },
    ));
  });
});
