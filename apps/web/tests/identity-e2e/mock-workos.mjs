// Minimal local stand-in for the WorkOS endpoints the official SDKs call.
// Test-only: lets the real RWA Guard BFF (AuthKit) and API (Python SDK + PyJWT)
// code paths run end to end without WorkOS credentials.
import { createServer } from 'node:http';
import { generateKeyPairSync, randomUUID, sign } from 'node:crypto';

const PORT = Number(process.env.MOCK_WORKOS_PORT ?? 4556);
const CLIENT_ID = process.env.MOCK_CLIENT_ID ?? 'client_01GUARDE2E';
const ISSUER = process.env.MOCK_ISSUER ?? `https://api.workos.test/user_management/${CLIENT_ID}`;
const KID = 'sso_oidc_key_pair_01E2E';
const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: KID, alg: 'RS256', use: 'sig' };

const users = new Map(); // id -> { email, first_name, last_name, orgs: [ids] }
const sessions = new Map(); // sid -> { user_id, org_id, status, auth_method, created_at }
const codes = new Map(); // code -> { user_id, org_id, auth_method }
const refreshTokens = new Map(); // token -> sid
let nextLogin = null;
const log = [];
const authorizations = []; // { max_age, organization_id } per /authorize (never the state or PKCE values)

const b64url = (buf) => Buffer.from(buf).toString('base64url');
function mintToken(sid) {
  const s = sessions.get(sid);
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'RS256', typ: 'JWT', kid: KID };
  const claims = { iss: ISSUER, sub: s.user_id, sid, org_id: s.org_id, role: 'member', permissions: [], iat: now, exp: now + 300, jti: randomUUID() };
  const body = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(claims))}`;
  return `${body}.${b64url(sign('RSA-SHA256', Buffer.from(body), privateKey))}`;
}
function userJson(id) {
  const u = users.get(id);
  const ts = new Date().toISOString();
  return { object: 'user', id, email: u.email, email_verified: true, first_name: u.first_name, last_name: u.last_name,
    profile_picture_url: null, last_sign_in_at: ts, created_at: ts, updated_at: ts, external_id: null, metadata: {} };
}
function authResponse(sid) {
  const s = sessions.get(sid);
  const refresh = `rt_${randomUUID().replace(/-/g, '')}`;
  refreshTokens.set(refresh, sid);
  return { user: userJson(s.user_id), organization_id: s.org_id, access_token: mintToken(sid), refresh_token: refresh, authentication_method: s.auth_method };
}
async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString('utf8');
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return Object.fromEntries(new URLSearchParams(raw)); }
}
function send(res, status, body, headers = {}) {
  res.writeHead(status, { 'content-type': 'application/json', ...headers });
  res.end(body === undefined ? '' : JSON.stringify(body));
}

createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const path = url.pathname;
  log.push(`${req.method} ${path}`);
  try {
    // ── test control ──
    if (path === '/__test/user' && req.method === 'POST') {
      const b = await readBody(req); users.set(b.id, b); return send(res, 200, { ok: true });
    }
    if (path === '/__test/next-login' && req.method === 'POST') {
      nextLogin = await readBody(req); return send(res, 200, { ok: true });
    }
    if (path === '/__test/sessions') return send(res, 200, Object.fromEntries(sessions));
    if (path === '/__test/log') return send(res, 200, log);
    if (path === '/__test/authorizations') return send(res, 200, authorizations);
    // ── WorkOS surface ──
    if (path === `/sso/jwks/${CLIENT_ID}`) return send(res, 200, { keys: [jwk] });
    if (path === '/user_management/authorize') {
      authorizations.push({ max_age: url.searchParams.get('max_age'), organization_id: url.searchParams.get('organization_id') });
      if (!nextLogin) return send(res, 400, { error: 'no_login_configured' });
      const code = `code_${randomUUID().replace(/-/g, '')}`;
      codes.set(code, nextLogin);
      const back = new URL(url.searchParams.get('redirect_uri'));
      back.searchParams.set('code', code);
      if (url.searchParams.get('state')) back.searchParams.set('state', url.searchParams.get('state'));
      res.writeHead(302, { location: back.toString() }); return res.end();
    }
    if (path === '/user_management/authenticate' && req.method === 'POST') {
      const b = await readBody(req);
      if (b.grant_type === 'authorization_code') {
        const login = codes.get(b.code); codes.delete(b.code);
        if (!login || !b.code_verifier) return send(res, 400, { error: 'invalid_grant' });
        const sid = `session_${randomUUID().replace(/-/g, '').slice(0, 24).toUpperCase()}`;
        sessions.set(sid, { user_id: login.user_id, org_id: login.org_id, status: 'active', auth_method: login.auth_method ?? 'password', created_at: new Date().toISOString() });
        return send(res, 200, authResponse(sid));
      }
      if (b.grant_type === 'refresh_token') {
        const sid = refreshTokens.get(b.refresh_token); refreshTokens.delete(b.refresh_token);
        const s = sid && sessions.get(sid);
        if (!s || s.status !== 'active') return send(res, 400, { error: 'invalid_grant', error_description: 'Session has ended.' });
        if (b.organization_id && b.organization_id !== s.org_id) {
          if (!(users.get(s.user_id)?.orgs ?? []).includes(b.organization_id)) return send(res, 403, { error: 'organization_membership_required' });
          s.org_id = b.organization_id;
        }
        return send(res, 200, authResponse(sid));
      }
      return send(res, 400, { error: 'unsupported_grant_type' });
    }
    const m = path.match(/^\/user_management\/users\/([^/]+)\/sessions$/);
    if (m) {
      const data = [...sessions.entries()].filter(([, s]) => s.user_id === m[1] && s.status === 'active').map(([id, s]) => ({
        object: 'session', id, ip_address: '127.0.0.1', user_agent: 'e2e', user_id: s.user_id, auth_method: s.auth_method, status: s.status,
        expires_at: new Date(Date.now() + 3600e3).toISOString(), ended_at: null, created_at: s.created_at, updated_at: s.created_at,
        impersonator: null, organization_id: s.org_id,
      }));
      return send(res, 200, { object: 'list', data, list_metadata: { before: null, after: null } });
    }
    if (path === '/user_management/sessions/logout') {
      const s = sessions.get(url.searchParams.get('session_id'));
      if (s) s.status = 'revoked';
      res.writeHead(302, { location: url.searchParams.get('return_to') ?? 'http://127.0.0.1/' }); return res.end();
    }
    return send(res, 404, { error: 'not_found', path });
  } catch (error) {
    return send(res, 500, { error: String(error) });
  }
}).listen(PORT, '127.0.0.1', () => console.log(`mock WorkOS on :${PORT} issuer=${ISSUER}`));
