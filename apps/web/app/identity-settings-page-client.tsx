'use client';

import { useEffect, useState } from 'react';
import { usePilotAuth } from 'app/pilot-auth-context';

type AccessControl = {
  role: string;
  permissions: string[];
  policy: { mfa_enforcement: string; reauthentication_minutes: number };
  matrix: Record<string, Record<string, boolean>>;
};

type OidcConfiguration = {
  issuer_url: string;
  client_id: string;
  email_domain: string | null;
  default_role: string;
  auto_provision: boolean;
  enabled: boolean;
};

export default function IdentitySettingsPageClient() {
  const { authHeaders } = usePilotAuth();
  const [access, setAccess] = useState<AccessControl | null>(null);
  const [oidc, setOidc] = useState<OidcConfiguration | null>(null);
  const [issuerUrl, setIssuerUrl] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [emailDomain, setEmailDomain] = useState('');
  const [mfaEnforcement, setMfaEnforcement] = useState('optional');
  const [reauthenticationMinutes, setReauthenticationMinutes] = useState(15);
  const [password, setPassword] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [scimLabel, setScimLabel] = useState('Identity provider');
  const [scimToken, setScimToken] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    const [accessResponse, oidcResponse] = await Promise.all([
      fetch('/api/workspace/access-control', { headers: authHeaders(), cache: 'no-store' }),
      fetch('/api/workspace/sso/oidc', { headers: authHeaders(), cache: 'no-store' }),
    ]);
    if (accessResponse.ok) {
      const payload = await accessResponse.json();
      setAccess(payload);
      setMfaEnforcement(payload.policy.mfa_enforcement);
      setReauthenticationMinutes(payload.policy.reauthentication_minutes);
    }
    if (oidcResponse.ok) {
      const payload = await oidcResponse.json();
      const configuration = payload.configuration ?? null;
      setOidc(configuration);
      setIssuerUrl(configuration?.issuer_url ?? '');
      setClientId(configuration?.client_id ?? '');
      setEmailDomain(configuration?.email_domain ?? '');
    }
  }

  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function reauthenticate() {
    const response = await fetch('/api/auth/reauthenticate', {
      method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, code: mfaCode }),
    });
    setMessage(response.ok ? 'Session reauthenticated. Sensitive changes are now available.' : 'Reauthentication failed.');
    return response.ok;
  }

  async function savePolicy() {
    const response = await fetch('/api/workspace/auth-policy', {
      method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ mfa_enforcement: mfaEnforcement, reauthentication_minutes: reauthenticationMinutes }),
    });
    setMessage(response.ok ? 'Workspace authentication policy saved.' : 'Reauthenticate, then retry the policy update.');
    if (response.ok) await load();
  }

  async function saveOidc() {
    const response = await fetch('/api/workspace/sso/oidc', {
      method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ issuer_url: issuerUrl, client_id: clientId, client_secret: clientSecret, email_domain: emailDomain, scopes: ['openid', 'profile', 'email'], auto_provision: true, default_role: 'viewer', enabled: true }),
    });
    setMessage(response.ok ? 'OIDC configuration saved.' : 'Unable to save OIDC. Reauthenticate and verify the issuer settings.');
    if (response.ok) { setClientSecret(''); await load(); }
  }

  async function createScimToken() {
    const response = await fetch('/api/workspace/scim/tokens', {
      method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: scimLabel }),
    });
    const payload = await response.json();
    if (response.ok) { setScimToken(payload.token); setMessage('SCIM token created. Copy it now; it will not be shown again.'); }
    else setMessage('Unable to create SCIM token. Reauthenticate and retry.');
  }

  const canManageIdentity = access?.permissions.includes('identity.manage') ?? false;
  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Workspace security</p><h1>Identity and access</h1></div></div>
        <p className="muted">Configure self-serve MFA enforcement, OIDC single sign-on, SCIM provisioning, and inspect the explicit role-permission matrix.</p>
        {message ? <p role="status">{message}</p> : null}
      </section>

      <section className="featureSection"><h2>Reauthenticate</h2><article className="dataCard">
        <p className="muted">Sensitive identity changes require a recent password and MFA verification.</p>
        <input type="password" placeholder="Current password" value={password} onChange={(event) => setPassword(event.target.value)} />
        <input inputMode="numeric" placeholder="MFA code (when enabled)" value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} />
        <button type="button" onClick={() => void reauthenticate()}>Reauthenticate</button>
      </article></section>

      <section className="featureSection"><h2>Administrative MFA enforcement</h2><article className="dataCard">
        <select value={mfaEnforcement} onChange={(event) => setMfaEnforcement(event.target.value)} disabled={!canManageIdentity}>
          <option value="optional">Optional</option><option value="administrators">Require for administrators</option><option value="all_members">Require for all members</option>
        </select>
        <input type="number" min={1} max={120} value={reauthenticationMinutes} onChange={(event) => setReauthenticationMinutes(Number(event.target.value))} />
        <button type="button" onClick={() => void savePolicy()} disabled={!canManageIdentity}>Save policy</button>
      </article></section>

      <section className="featureSection"><h2>OIDC single sign-on</h2><article className="dataCard">
        <input placeholder="https://idp.example.com" value={issuerUrl} onChange={(event) => setIssuerUrl(event.target.value)} />
        <input placeholder="Client ID" value={clientId} onChange={(event) => setClientId(event.target.value)} />
        <input type="password" placeholder={oidc ? 'Leave blank to keep current secret' : 'Client secret'} value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} />
        <input placeholder="Allowed email domain" value={emailDomain} onChange={(event) => setEmailDomain(event.target.value)} />
        <button type="button" onClick={() => void saveOidc()} disabled={!canManageIdentity}>Save OIDC</button>
      </article></section>

      <section className="featureSection"><h2>SCIM provisioning</h2><article className="dataCard">
        <p className="muted">Use the SCIM 2.0 base path <code>/scim/v2</code> in your identity provider.</p>
        <input placeholder="Token label" value={scimLabel} onChange={(event) => setScimLabel(event.target.value)} />
        <button type="button" onClick={() => void createScimToken()} disabled={!canManageIdentity}>Create SCIM token</button>
        {scimToken ? <pre>{scimToken}</pre> : null}
      </article></section>

      <section className="featureSection"><h2>Role permissions</h2><article className="dataCard">
        <table><thead><tr><th>Role</th>{Object.keys(access?.matrix.owner ?? {}).map((permission) => <th key={permission}>{permission}</th>)}</tr></thead>
          <tbody>{Object.entries(access?.matrix ?? {}).map(([role, permissions]) => <tr key={role}><td>{role}</td>{Object.entries(permissions).map(([permission, granted]) => <td key={permission}>{granted ? 'Allowed' : 'Denied'}</td>)}</tr>)}</tbody>
        </table>
      </article></section>
    </main>
  );
}
