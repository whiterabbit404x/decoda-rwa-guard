'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { classifyAuthResponseError, classifyAuthTransportError } from './auth-diagnostics';
import type { RuntimeConfig } from './runtime-config-schema';

const CSRF_COOKIE_NAME = 'decoda-csrf-token';
const UNLOADED_RUNTIME_CONFIG: RuntimeConfig = {
  apiUrl: null,
  liveModeEnabled: false,
  apiTimeoutMs: null,
  configured: false,
  diagnostic: null,
  source: { apiUrl: 'missing', liveModeEnabled: 'missing', apiTimeoutMs: 'missing' },
};

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const part = document.cookie.split(';').map((x) => x.trim()).find((x) => x.startsWith(`${name}=`));
  return part ? decodeURIComponent(part.slice(name.length + 1)) : null;
}

export type WorkspaceSummary = { id: string; name: string; slug: string };
export type WorkspaceMembership = { workspace_id: string; role: 'owner' | 'admin' | 'analyst' | 'viewer'; created_at: string; workspace: WorkspaceSummary };
export type PilotUser = {
  id: string; email: string; full_name: string; current_workspace_id: string | null; created_at: string; updated_at: string; last_sign_in_at: string | null;
  email_verified: boolean; email_verified_at: string | null; mfa_enabled: boolean; current_workspace: WorkspaceSummary | null; memberships: WorkspaceMembership[];
};

type PilotAuthContextValue = {
  apiUrl: string; apiTimeoutMs: number | null; configured: boolean; configLoading: boolean; liveModeConfigured: boolean; liveModeEnabled: boolean; loading: boolean;
  token: string | null; user: PilotUser | null; error: string | null; runtimeConfigDiagnostic: string | null; runtimeConfigSource: RuntimeConfig['source'];
  isAuthenticated: boolean; mfaChallengeToken: string | null;
  signIn: (payload: { email: string; password: string }) => Promise<PilotUser>;
  completeMfaSignIn: (code: string) => Promise<PilotUser>; enrollMfa: () => Promise<{ otpauth_uri: string; secret: string | null }>;
  confirmMfaEnrollment: (code: string) => Promise<{ recovery_codes: string[] }>; disableMfa: (code: string) => Promise<void>;
  signUp: (payload: { email: string; password: string; full_name: string; workspace_name: string }) => Promise<{ user: PilotUser | null; verificationRequired: boolean }>;
  signOut: () => Promise<void>; refreshUser: () => Promise<PilotUser | null>; createWorkspace: (name: string) => Promise<PilotUser>; selectWorkspace: (workspaceId: string) => Promise<PilotUser>;
  authHeaders: () => Record<string, string>; setError: (value: string | null) => void;
};
const PilotAuthContext = createContext<PilotAuthContextValue | null>(null);

async function readApiResponse<T>(response: Response): Promise<Partial<T> & { detail?: string; message?: string }> {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try { return (await response.json()) as Partial<T> & { detail?: string; message?: string }; } catch { return { detail: `Request failed with HTTP ${response.status}` }; }
  }
  return { detail: `Request failed with HTTP ${response.status}` };
}

export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const response = await fetch('/api/runtime-config', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Runtime config request failed with HTTP ${response.status}.`);
  const data = await readApiResponse<RuntimeConfig>(response);
  if (!('configured' in data) || !('source' in data) || !('liveModeEnabled' in data)) {
    throw new Error(data.detail ?? `Runtime config request failed with HTTP ${response.status}.`);
  }
  return data as RuntimeConfig;
}

export function PilotAuthProvider({ children }: { children: React.ReactNode }) {
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(UNLOADED_RUNTIME_CONFIG);
  const [configLoading, setConfigLoading] = useState(true);
  const [user, setUser] = useState<PilotUser | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mfaChallengeToken, setMfaChallengeToken] = useState<string | null>(null);

  const authHeaders = useCallback(() => {
    const headers: Record<string, string> = {};
    if (user?.current_workspace?.id) headers['X-Workspace-Id'] = user.current_workspace.id;
    const csrf = readCookie(CSRF_COOKIE_NAME);
    if (csrf) headers['X-CSRF-Token'] = csrf;
    return headers;
  }, [user?.current_workspace?.id]);

  const refreshUser = useCallback(async () => {
    if (configLoading) return null;
    const response = await fetch('/api/auth/me', { cache: 'no-store' });
    if (!response.ok) {
      setUser(null); setMfaChallengeToken(null); setSessionLoading(false);
      if (response.status !== 401) setError('Unable to restore your session. Please sign in again.');
      return null;
    }
    const payload = await readApiResponse<{ user?: PilotUser }>(response);
    if (!payload.user) { setUser(null); setSessionLoading(false); return null; }
    setUser(payload.user); setSessionLoading(false); return payload.user;
  }, [configLoading]);

  useEffect(() => { let active = true; void fetchRuntimeConfig().then((c) => { if (active) setRuntimeConfig(c); }).catch((e) => active && setError(e instanceof Error ? e.message : 'Unable to load runtime auth configuration')).finally(() => active && setConfigLoading(false)); return () => { active = false; }; }, []);
  useEffect(() => { if (configLoading) return; setSessionLoading(true); void refreshUser(); }, [configLoading, refreshUser]);

  const signIn = useCallback(async (payload: { email: string; password: string }) => {
    const proxyUrl = '/api/auth/signin';
    let response: Response;
    try { response = await fetch(proxyUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); }
    catch (submitError) { throw new Error(classifyAuthTransportError('sign in', proxyUrl, submitError)); }
    const data = await readApiResponse<{ user?: PilotUser; mfa_required?: boolean; mfa_token?: string; detail?: string }>(response);
    if (!response.ok) throw new Error(classifyAuthResponseError('sign in', proxyUrl, response.status, data.detail, data));
    if (data.mfa_required) { setMfaChallengeToken(data.mfa_token ?? null); throw new Error('MFA_REQUIRED'); }
    await refreshUser();
    if (!data.user) {
      if (!user) throw new Error('Sign-in completed but session could not be restored.');
      return user;
    }
    setUser(data.user);
    return data.user;
  }, [refreshUser, user]);

  const completeMfaSignIn = useCallback(async (code: string) => {
    if (!mfaChallengeToken) throw new Error('MFA challenge expired. Sign in again.');
    const response = await fetch('/api/auth/mfa/complete-signin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mfa_token: mfaChallengeToken, code }) });
    const data = await readApiResponse<{ user?: PilotUser; detail?: string }>(response);
    if (!response.ok) throw new Error(data.detail ?? 'Invalid MFA code.');
    setMfaChallengeToken(null); await refreshUser();
    return data.user ?? (user as PilotUser);
  }, [mfaChallengeToken, refreshUser, user]);

  const enrollMfa = useCallback(async () => {
    const response = await fetch('/api/auth/mfa/enroll', { method: 'POST', headers: authHeaders() });
    const data = await readApiResponse<{ otpauth_uri?: string; secret?: string | null; detail?: string }>(response);
    if (!response.ok || !data.otpauth_uri) throw new Error(data.detail ?? 'Unable to start MFA enrollment.');
    return { otpauth_uri: data.otpauth_uri, secret: data.secret ?? null };
  }, [authHeaders]);

  const confirmMfaEnrollment = useCallback(async (code: string) => {
    const response = await fetch('/api/auth/mfa/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ code }) });
    const data = await readApiResponse<{ mfa_enabled?: boolean; recovery_codes?: string[]; detail?: string }>(response);
    if (!response.ok || !data.mfa_enabled) throw new Error(data.detail ?? 'Unable to confirm MFA enrollment.');
    await refreshUser(); return { recovery_codes: data.recovery_codes ?? [] };
  }, [authHeaders, refreshUser]);

  const disableMfa = useCallback(async (code: string) => {
    const response = await fetch('/api/auth/mfa/disable', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ code }) });
    const data = await readApiResponse<{ detail?: string }>(response);
    if (!response.ok) throw new Error(data.detail ?? 'Unable to disable MFA.');
    await refreshUser();
  }, [authHeaders, refreshUser]);

  const signUp = useCallback(async (payload: { email: string; password: string; full_name: string; workspace_name: string }) => {
    const proxyUrl = '/api/auth/signup';
    const response = await fetch(proxyUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await readApiResponse<{ user?: PilotUser; verification_required?: boolean; detail?: string }>(response);
    if (!response.ok) throw new Error(classifyAuthResponseError('create an account', proxyUrl, response.status, data.detail, data));
    if (data.verification_required) { setUser(null); return { user: null, verificationRequired: true }; }
    await refreshUser();
    return { user: data.user ?? user, verificationRequired: false };
  }, [refreshUser, user]);

  const signOut = useCallback(async () => {
    await fetch('/api/auth/signout', { method: 'POST', headers: authHeaders() }).catch(() => undefined);
    setUser(null); setMfaChallengeToken(null); setError(null); setSessionLoading(false);
  }, [authHeaders]);

  const createWorkspace = useCallback(async (name: string) => {
    const response = await fetch('/api/auth/workspaces', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ name }) });
    const data = await readApiResponse<{ user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.user) throw new Error(data.detail ?? 'Unable to create workspace.');
    setUser(data.user); return data.user;
  }, [authHeaders]);

  const selectWorkspace = useCallback(async (workspaceId: string) => {
    const response = await fetch('/api/auth/select-workspace', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ workspace_id: workspaceId }) });
    const data = await readApiResponse<{ user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.user) throw new Error(data.detail ?? 'Unable to select workspace.');
    setUser(data.user); return data.user;
  }, [authHeaders]);

  const loading = configLoading || sessionLoading;
  const value = useMemo<PilotAuthContextValue>(() => ({
    apiUrl: runtimeConfig.apiUrl ?? '', apiTimeoutMs: runtimeConfig.apiTimeoutMs, configured: runtimeConfig.configured, configLoading,
    liveModeConfigured: runtimeConfig.configured, liveModeEnabled: runtimeConfig.liveModeEnabled, loading,
    token: null, user, error, runtimeConfigDiagnostic: runtimeConfig.diagnostic, runtimeConfigSource: runtimeConfig.source,
    isAuthenticated: Boolean(user), mfaChallengeToken, signIn, completeMfaSignIn, enrollMfa, confirmMfaEnrollment, disableMfa,
    signUp, signOut, refreshUser, createWorkspace, selectWorkspace, authHeaders, setError,
  }), [runtimeConfig, configLoading, loading, user, error, mfaChallengeToken, signIn, completeMfaSignIn, enrollMfa, confirmMfaEnrollment, disableMfa, signUp, signOut, refreshUser, createWorkspace, selectWorkspace, authHeaders]);

  return <PilotAuthContext.Provider value={value}>{children}</PilotAuthContext.Provider>;
}

export function usePilotAuth() {
  const value = useContext(PilotAuthContext);
  if (!value) throw new Error('usePilotAuth must be used within PilotAuthProvider');
  return value;
}
