'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { classifyAuthResponseError, classifyAuthTransportError } from './auth-diagnostics';
import { normalizeWorkspaceHeaderValue } from './workspace-header';
import type { RuntimeConfig } from './runtime-config-schema';

const UNLOADED_RUNTIME_CONFIG: RuntimeConfig = {
  apiUrl: null,
  liveModeEnabled: false,
  apiTimeoutMs: null,
  configured: false,
  diagnostic: null,
  source: {
    apiUrl: 'missing',
    liveModeEnabled: 'missing',
    apiTimeoutMs: 'missing',
  },
};

const ACCESS_TOKEN_COOKIE_NAME = 'decoda_access_token';
const ACCESS_TOKEN_STORAGE_KEY = 'decoda.accessToken';
const MISSING_SESSION_MESSAGE = 'Your session is missing or expired. Please sign in again.';

export type WorkspaceSummary = {
  id: string;
  name: string;
  slug: string;
};

export type WorkspaceMembership = {
  workspace_id: string;
  role: 'owner' | 'admin' | 'analyst' | 'viewer';
  created_at: string;
  workspace: WorkspaceSummary;
};

export type PilotUser = {
  id: string;
  email: string;
  full_name: string;
  current_workspace_id: string | null;
  created_at: string;
  updated_at: string;
  last_sign_in_at: string | null;
  email_verified: boolean;
  email_verified_at: string | null;
  mfa_enabled: boolean;
  current_workspace: WorkspaceSummary | null;
  memberships: WorkspaceMembership[];
};

type PilotAuthContextValue = {
  apiUrl: string;
  apiTimeoutMs: number | null;
  configured: boolean;
  configLoading: boolean;
  liveModeConfigured: boolean;
  liveModeEnabled: boolean;
  loading: boolean;
  user: PilotUser | null;
  error: string | null;
  runtimeConfigDiagnostic: string | null;
  runtimeConfigSource: RuntimeConfig['source'];
  isAuthenticated: boolean;
  mfaChallengeToken: string | null;
  signIn: (payload: { email: string; password: string }) => Promise<PilotUser>;
  completeMfaSignIn: (code: string) => Promise<PilotUser>;
  enrollMfa: () => Promise<{ otpauth_uri: string; secret: string | null }>;
  confirmMfaEnrollment: (code: string) => Promise<{ recovery_codes: string[] }>;
  disableMfa: (code: string) => Promise<void>;
  signUp: (payload: { email: string; password: string; full_name: string; workspace_name: string }) => Promise<{ user: PilotUser | null; verificationRequired: boolean }>;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<PilotUser | null>;
  createWorkspace: (name: string) => Promise<PilotUser>;
  selectWorkspace: (workspaceId: string) => Promise<PilotUser>;
  authHeaders: (workspaceIdOverride?: string | null) => Record<string, string>;
  setError: (value: string | null) => void;
};

const PilotAuthContext = createContext<PilotAuthContextValue | null>(null);

type ApiResponsePayload<T extends object = Record<string, never>> = Partial<T> & {
  detail?: string;
  message?: string;
};

function makeApiResponseFallback<T extends object>(detail: string): ApiResponsePayload<T> {
  return { detail } as ApiResponsePayload<T>;
}

async function readApiResponse<T extends object = Record<string, never>>(response: Response): Promise<ApiResponsePayload<T>> {
  const contentType = response.headers.get('content-type') || '';

  if (contentType.includes('application/json')) {
    try {
      return (await response.json()) as ApiResponsePayload<T>;
    } catch {
      return makeApiResponseFallback<T>(`Request failed with HTTP ${response.status}`);
    }
  }

  try {
    const text = await response.text();
    return makeApiResponseFallback<T>(text ? 'Request failed. Please try again.' : `Request failed with HTTP ${response.status}`);
  } catch {
    return makeApiResponseFallback<T>(`Request failed with HTTP ${response.status}`);
  }
}

function safeAuthFailureMessage(message: string, fallback: string) {
  const normalized = message.trim();
  if (!normalized) {
    return fallback;
  }
  if (normalized.startsWith('{') || normalized.startsWith('[') || normalized.toLowerCase().includes('traceback')) {
    return fallback;
  }
  return normalized;
}

function readCookie(name: string) {
  if (typeof document === 'undefined') {
    return null;
  }
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = document.cookie.match(new RegExp(`(?:^|; )${escapedName}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function readStoredAccessToken() {
  if (typeof window === 'undefined') {
    return null;
  }
  const fromStorage = window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)?.trim() || null;
  if (fromStorage) {
    return fromStorage;
  }
  const fromCookie = readCookie(ACCESS_TOKEN_COOKIE_NAME)?.trim() || null;
  if (fromCookie) {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, fromCookie);
  }
  return fromCookie;
}

function persistAccessToken(token: string | null) {
  if (typeof window === 'undefined') {
    return;
  }
  if (!token) {
    window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token);
}

export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const response = await fetch('/api/runtime-config', {
    cache: 'no-store',
  });

  if (!response.ok) {
    throw new Error(`Runtime config request failed with HTTP ${response.status}.`);
  }

  const data = await readApiResponse<RuntimeConfig>(response);

  if (!('configured' in data) || !('source' in data) || !('liveModeEnabled' in data)) {
    throw new Error(data.detail ?? `Runtime config request failed with HTTP ${response.status}.`);
  }

  return data as RuntimeConfig;
}

export function PilotAuthProvider({ children }: { children: React.ReactNode }) {
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(UNLOADED_RUNTIME_CONFIG);
  const [configLoading, setConfigLoading] = useState(true);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [user, setUser] = useState<PilotUser | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mfaChallengeToken, setMfaChallengeToken] = useState<string | null>(null);
  const sessionRestoreStarted = useRef(false);

  const requireApiUrl = useCallback(() => {
    if (configLoading) {
      throw new Error('Runtime auth configuration is still loading. Please wait a moment and retry.');
    }

    if (!runtimeConfig.apiUrl) {
      throw new Error(runtimeConfig.diagnostic ?? 'Live API URL is not configured for this deployment.');
    }

    return runtimeConfig.apiUrl;
  }, [configLoading, runtimeConfig.apiUrl, runtimeConfig.diagnostic]);

  const authHeaders = useCallback((workspaceIdOverride?: string | null) => {
    const token = accessToken || readStoredAccessToken();
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const workspaceId = workspaceIdOverride ?? user?.current_workspace?.id ?? user?.current_workspace_id ?? null;
    const normalizedWorkspaceId = normalizeWorkspaceHeaderValue(workspaceId);
    if (normalizedWorkspaceId) {
      headers['X-Workspace-Id'] = normalizedWorkspaceId;
    }
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    return headers;
  }, [accessToken, csrfToken, user?.current_workspace?.id, user?.current_workspace_id]);

  const refreshUser = useCallback(async () => {
    if (typeof window === 'undefined') {
      return null;
    }

    if (configLoading) {
      return null;
    }

    console.debug('[dashboard-page-data trace] source=auth-session-restore', {
      phase: 'request',
      path: '/api/auth/me',
    });
    const response = await fetch('/api/auth/me', { cache: 'no-store' });

    if (!response.ok) {
      const data = await readApiResponse<{ detail?: string }>(response).catch((): ApiResponsePayload<{ detail?: string }> => ({
        detail: 'Your session expired. Please sign in again.',
      }));
      setUser(null);
      setMfaChallengeToken(null);
      setAccessToken(null);
      persistAccessToken(null);
      setError(response.status === 401 ? MISSING_SESSION_MESSAGE : (data.detail ?? MISSING_SESSION_MESSAGE));
      setSessionLoading(false);
      console.debug('[dashboard-page-data trace] source=auth-session-restore', {
        phase: 'response-error',
        status: response.status,
      });
      return null;
    }

    const payload = await readApiResponse<{ user?: PilotUser; detail?: string }>(response);
    if (!payload.user) {
      setUser(null);
      setMfaChallengeToken(null);
      setAccessToken(null);
      persistAccessToken(null);
      setError(payload.detail ?? MISSING_SESSION_MESSAGE);
      setSessionLoading(false);
      console.debug('[dashboard-page-data trace] source=auth-session-restore', {
        phase: 'response-missing-user',
      });
      return null;
    }
    setUser(payload.user);
    const restoredToken = readStoredAccessToken();
    if (restoredToken) {
      setAccessToken(restoredToken);
    }
    const csrfResponse = await fetch('/api/auth/csrf', { cache: 'no-store' });
    if (csrfResponse.ok) {
      const csrfPayload = await csrfResponse.json().catch(() => ({}));
      setCsrfToken(typeof csrfPayload.csrfToken === 'string' ? csrfPayload.csrfToken : null);
    }
    setSessionLoading(false);
    console.debug('[dashboard-page-data trace] source=auth-session-restore', {
      phase: 'response-success',
      hasCurrentWorkspace: Boolean(payload.user.current_workspace),
      userId: payload.user.id,
    });
    return payload.user;
  }, [configLoading]);

  useEffect(() => {
    let active = true;

    void fetchRuntimeConfig()
      .then((nextRuntimeConfig) => {
        if (!active) {
          return;
        }
        setRuntimeConfig(nextRuntimeConfig);
        if (nextRuntimeConfig.diagnostic) {
          setError((currentError) => currentError ?? nextRuntimeConfig.diagnostic);
        }
      })
      .catch((fetchError) => {
        if (!active) {
          return;
        }

        const message = fetchError instanceof Error
          ? fetchError.message
          : 'Unable to load runtime auth configuration for this deployment.';

        setRuntimeConfig({
          ...UNLOADED_RUNTIME_CONFIG,
          diagnostic: message,
          source: {
            apiUrl: 'invalid',
            liveModeEnabled: 'invalid',
            apiTimeoutMs: 'invalid',
          },
        });
        setError(message);
      })
      .finally(() => {
        if (active) {
          setConfigLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (configLoading) {
      return;
    }

    if (sessionRestoreStarted.current) {
      return;
    }

    sessionRestoreStarted.current = true;
    setSessionLoading(true);
    console.debug('[dashboard-page-data trace] source=auth-session-restore', {
      phase: 'effect-start',
      configLoading,
    });
    void refreshUser().catch((fetchError) => {
      const message = fetchError instanceof Error ? fetchError.message : String(fetchError);
      console.debug('[dashboard-page-data trace] source=auth-session-restore', {
        phase: 'effect-catch',
        message,
      });
      setError(safeAuthFailureMessage(message, MISSING_SESSION_MESSAGE));
      setSessionLoading(false);
    });
  }, [configLoading, refreshUser]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    const restored = readStoredAccessToken();
    if (restored) {
      setAccessToken(restored);
    }
  }, []);

  const saveAuthPayload = useCallback((nextUser: PilotUser, nextAccessToken?: string | null) => {
    setUser(nextUser);
    const token = (nextAccessToken ?? '').trim() || readStoredAccessToken();
    setAccessToken(token);
    persistAccessToken(token);
    setError(null);
  }, []);

  const signIn = useCallback(async (payload: { email: string; password: string }) => {
    const proxyUrl = '/api/auth/signin';
    let response: Response;

    try {
      response = await fetch(proxyUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (submitError) {
      throw new Error(classifyAuthTransportError('sign in', proxyUrl, submitError));
    }

    const data = await readApiResponse<{
      user?: PilotUser;
      mfa_required?: boolean;
      mfa_token?: string;
      detail?: string;
      authTransport?: string;
      backendApiUrl?: string | null;
      configured?: boolean;
      code?: string;
      access_token?: string;
    }>(response);
    if (!response.ok) {
      throw new Error(classifyAuthResponseError('sign in', proxyUrl, response.status, data.detail, data));
    }
    if (data.mfa_required) {
      if (!data.mfa_token) {
        throw new Error('MFA challenge could not be created. Please try signing in again.');
      }
      setMfaChallengeToken(data.mfa_token);
      throw new Error('MFA_REQUIRED');
    }
    if (!data.user) {
      throw new Error(classifyAuthResponseError('sign in', proxyUrl, response.status, data.detail, data));
    }
    saveAuthPayload(data.user, data.access_token ?? null);
    setMfaChallengeToken(null);
    console.debug('[dashboard-page-data trace] source=auth-signin-response', {
      phase: 'success',
      hasUser: Boolean(data.user),
      userId: data.user.id,
      requiresSessionConfirmation: true,
    });
    return data.user;
  }, [saveAuthPayload]);

  const completeMfaSignIn = useCallback(async (code: string) => {
    if (!mfaChallengeToken) {
      throw new Error('MFA challenge expired. Sign in again.');
    }
    const proxyUrl = '/api/auth/mfa/complete-signin';
    const response = await fetch(proxyUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mfa_token: mfaChallengeToken, code }),
    });
    const data = await readApiResponse<{ user?: PilotUser; detail?: string; access_token?: string }>(response);
    if (!response.ok || !data.user) {
      throw new Error(data.detail ?? 'Invalid MFA code.');
    }
    saveAuthPayload(data.user, data.access_token ?? null);
    setMfaChallengeToken(null);
    console.debug('[dashboard-page-data trace] source=auth-signin-response', {
      phase: 'mfa-success',
      hasUser: Boolean(data.user),
      userId: data.user.id,
      requiresSessionConfirmation: true,
    });
    return data.user;
  }, [mfaChallengeToken, saveAuthPayload]);

  const enrollMfa = useCallback(async () => {
    const response = await fetch('/api/auth/mfa/enroll', {
      method: 'POST',
      headers: authHeaders(),
    });
    const data = await readApiResponse<{ otpauth_uri?: string; secret?: string | null; detail?: string }>(response);
    if (!response.ok || !data.otpauth_uri) {
      throw new Error(data.detail ?? 'Unable to start MFA enrollment.');
    }
    return { otpauth_uri: data.otpauth_uri, secret: data.secret ?? null };
  }, [authHeaders]);

  const confirmMfaEnrollment = useCallback(async (code: string) => {
    const response = await fetch('/api/auth/mfa/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ code }),
    });
    const data = await readApiResponse<{ mfa_enabled?: boolean; recovery_codes?: string[]; detail?: string }>(response);
    if (!response.ok || !data.mfa_enabled) {
      throw new Error(data.detail ?? 'Unable to confirm MFA enrollment.');
    }
    await refreshUser();
    return { recovery_codes: data.recovery_codes ?? [] };
  }, [authHeaders, refreshUser]);

  const disableMfa = useCallback(async (code: string) => {
    const response = await fetch('/api/auth/mfa/disable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ code }),
    });
    const data = await readApiResponse<{ detail?: string }>(response);
    if (!response.ok) {
      throw new Error(data.detail ?? 'Unable to disable MFA.');
    }
    await refreshUser();
  }, [authHeaders, refreshUser]);

  const signUp = useCallback(async (payload: { email: string; password: string; full_name: string; workspace_name: string }) => {
    const proxyUrl = '/api/auth/signup';
    let response: Response;

    try {
      response = await fetch(proxyUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (submitError) {
      throw new Error(classifyAuthTransportError('create an account', proxyUrl, submitError));
    }

    const data = await readApiResponse<{
      user?: PilotUser;
      verification_required?: boolean;
      detail?: string;
      authTransport?: string;
      backendApiUrl?: string | null;
      configured?: boolean;
      code?: string;
      access_token?: string;
    }>(response);
    if (!response.ok) {
      throw new Error(classifyAuthResponseError('create an account', proxyUrl, response.status, data.detail, data));
    }

    if (data.verification_required) {
      setUser(null);
      setError('Account created. Verify your email before signing in.');
      return { user: null, verificationRequired: true };
    }

    if (!data.user) {
      throw new Error(classifyAuthResponseError('create an account', proxyUrl, response.status, data.detail, data));
    }
    saveAuthPayload(data.user, data.access_token ?? null);
    const csrfResponse = await fetch('/api/auth/csrf', { cache: 'no-store' });
    if (csrfResponse.ok) {
      const csrfPayload = await csrfResponse.json().catch(() => ({}));
      setCsrfToken(typeof csrfPayload.csrfToken === 'string' ? csrfPayload.csrfToken : null);
    }
    return { user: data.user, verificationRequired: false };
  }, [saveAuthPayload]);

  const signOut = useCallback(async () => {
    await fetch('/api/auth/signout', { method: 'POST', headers: authHeaders() }).catch(() => undefined);
    setUser(null);
    setMfaChallengeToken(null);
    setCsrfToken(null);
    setAccessToken(null);
    persistAccessToken(null);
    setError(null);
    setSessionLoading(false);
  }, [authHeaders]);

  const createWorkspace = useCallback(async (name: string) => {
    const response = await fetch('/api/auth/workspaces', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify({ name }),
    });
    const data = await readApiResponse<{ user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.user) {
      if (response.status === 401) {
        await signOut();
      }
      throw new Error(data.detail ?? 'Unable to create workspace.');
    }
    setUser(data.user);
    return data.user;
  }, [authHeaders, signOut]);

  const selectWorkspace = useCallback(async (workspaceId: string) => {
    const response = await fetch('/api/auth/select-workspace', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    const data = await readApiResponse<{ user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.user) {
      if (response.status === 401) {
        await signOut();
      }
      throw new Error(data.detail ?? 'Unable to select workspace.');
    }
    setUser(data.user);
    return data.user;
  }, [authHeaders, signOut]);

  const loading = configLoading || sessionLoading;

  const value = useMemo<PilotAuthContextValue>(() => ({
    apiUrl: runtimeConfig.apiUrl ?? '',
    apiTimeoutMs: runtimeConfig.apiTimeoutMs,
    configured: runtimeConfig.configured,
    configLoading,
    liveModeConfigured: runtimeConfig.configured,
    liveModeEnabled: runtimeConfig.liveModeEnabled,
    loading,
    user,
    error,
    runtimeConfigDiagnostic: runtimeConfig.diagnostic,
    runtimeConfigSource: runtimeConfig.source,
    isAuthenticated: Boolean(user),
    mfaChallengeToken,
    signIn,
    completeMfaSignIn,
    enrollMfa,
    confirmMfaEnrollment,
    disableMfa,
    signUp,
    signOut,
    refreshUser,
    createWorkspace,
    selectWorkspace,
    authHeaders,
    setError,
  }), [authHeaders, completeMfaSignIn, configLoading, confirmMfaEnrollment, createWorkspace, disableMfa, enrollMfa, error, loading, mfaChallengeToken, refreshUser, runtimeConfig, selectWorkspace, signIn, signOut, signUp, user]);

  return <PilotAuthContext.Provider value={value}>{children}</PilotAuthContext.Provider>;
}

export function usePilotAuth() {
  const value = useContext(PilotAuthContext);
  if (!value) {
    throw new Error('usePilotAuth must be used within PilotAuthProvider');
  }

  return value;
}
