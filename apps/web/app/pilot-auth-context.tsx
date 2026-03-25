'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { classifyAuthResponseError, classifyAuthTransportError } from './auth-diagnostics';
import type { RuntimeConfig } from './runtime-config-schema';

const TOKEN_STORAGE_KEY = 'decoda-pilot-access-token';
const TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24;
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

function writeTokenCookie(token: string | null) {
  if (typeof document === 'undefined') {
    return;
  }

  if (!token) {
    document.cookie = `${TOKEN_STORAGE_KEY}=; path=/; max-age=0; SameSite=Lax`;
    return;
  }

  document.cookie = `${TOKEN_STORAGE_KEY}=${encodeURIComponent(token)}; path=/; max-age=${TOKEN_COOKIE_MAX_AGE}; SameSite=Lax`;
}

export type WorkspaceSummary = {
  id: string;
  name: string;
  slug: string;
};

export type WorkspaceMembership = {
  workspace_id: string;
  role: 'workspace_owner' | 'workspace_admin' | 'workspace_member' | 'workspace_viewer';
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
  email_verified?: boolean;
  email_verified_at?: string | null;
  subscription?: {
    plan: 'trial' | 'starter' | 'growth' | 'enterprise';
    status: 'trialing' | 'active' | 'past_due' | 'canceled' | 'incomplete';
    trial_ends_at: string | null;
  };
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
  token: string | null;
  user: PilotUser | null;
  error: string | null;
  runtimeConfigDiagnostic: string | null;
  runtimeConfigSource: RuntimeConfig['source'];
  isAuthenticated: boolean;
  signIn: (payload: { email: string; password: string }) => Promise<PilotUser>;
  signUp: (payload: { email: string; password: string; full_name: string; workspace_name: string }) => Promise<PilotUser>;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<PilotUser | null>;
  createWorkspace: (name: string) => Promise<PilotUser>;
  selectWorkspace: (workspaceId: string) => Promise<PilotUser>;
  authHeaders: () => Record<string, string>;
  setError: (value: string | null) => void;
};

const PilotAuthContext = createContext<PilotAuthContextValue | null>(null);

async function readApiResponse<T>(response: Response): Promise<Partial<T> & { detail?: string; message?: string }> {
  const contentType = response.headers.get('content-type') || '';

  if (contentType.includes('application/json')) {
    try {
      return (await response.json()) as Partial<T> & { detail?: string; message?: string };
    } catch {
      return { detail: `Request failed with HTTP ${response.status}` } as Partial<T> & { detail?: string; message?: string };
    }
  }

  try {
    const text = await response.text();
    return { detail: text ? 'Request failed. Please try again.' : `Request failed with HTTP ${response.status}` } as Partial<T> & { detail?: string; message?: string };
  } catch {
    return { detail: `Request failed with HTTP ${response.status}` } as Partial<T> & { detail?: string; message?: string };
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
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<PilotUser | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const requireApiUrl = useCallback(() => {
    if (configLoading) {
      throw new Error('Runtime auth configuration is still loading. Please wait a moment and retry.');
    }

    if (!runtimeConfig.apiUrl) {
      throw new Error(runtimeConfig.diagnostic ?? 'Live API URL is not configured for this deployment.');
    }

    return runtimeConfig.apiUrl;
  }, [configLoading, runtimeConfig.apiUrl, runtimeConfig.diagnostic]);

  const authHeaders = useCallback(() => {
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    if (user?.current_workspace?.id) {
      headers['X-Workspace-Id'] = user.current_workspace.id;
    }
    return headers;
  }, [token, user?.current_workspace?.id]);

  const refreshUser = useCallback(async () => {
    if (typeof window === 'undefined') {
      return null;
    }

    if (configLoading) {
      return null;
    }

    const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (!storedToken) {
      setToken(null);
      setUser(null);
      setSessionLoading(false);
      return null;
    }

    setToken(storedToken);
    const response = await fetch('/api/auth/me', {
      headers: {
        Authorization: `Bearer ${storedToken}`,
      },
      cache: 'no-store',
    });

    if (!response.ok) {
      const data = await readApiResponse<{ detail?: string }>(response).catch(() => ({ detail: 'Your session expired. Please sign in again.' }));
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      writeTokenCookie(null);
      setToken(null);
      setUser(null);
      setError(response.status === 401 ? 'Your session expired. Please sign in again.' : (data.detail ?? 'Unable to restore your session. Please sign in again.'));
      setSessionLoading(false);
      return null;
    }

    const payload = await readApiResponse<{ user?: PilotUser; detail?: string }>(response);
    if (!payload.user) {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      writeTokenCookie(null);
      setToken(null);
      setUser(null);
      setError(payload.detail ?? 'Your session expired. Please sign in again.');
      setSessionLoading(false);
      return null;
    }
    setUser(payload.user);
    setSessionLoading(false);
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

    setSessionLoading(true);
    void refreshUser().catch((fetchError) => {
      const message = fetchError instanceof Error ? fetchError.message : String(fetchError);
      setError(safeAuthFailureMessage(message, 'Unable to restore your session. Please sign in again.'));
      setSessionLoading(false);
    });
  }, [configLoading, refreshUser]);

  const saveAuthPayload = useCallback((accessToken: string, nextUser: PilotUser) => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, accessToken);
    writeTokenCookie(accessToken);
    setToken(accessToken);
    setUser(nextUser);
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
      access_token?: string;
      user?: PilotUser;
      detail?: string;
      authTransport?: string;
      backendApiUrl?: string | null;
      configured?: boolean;
      code?: string;
    }>(response);
    if (!response.ok || !data.access_token || !data.user) {
      throw new Error(classifyAuthResponseError('sign in', proxyUrl, response.status, data.detail, data));
    }
    saveAuthPayload(data.access_token, data.user);
    return data.user;
  }, [saveAuthPayload]);

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
      access_token?: string;
      user?: PilotUser;
      detail?: string;
      authTransport?: string;
      backendApiUrl?: string | null;
      configured?: boolean;
      code?: string;
    }>(response);
    if (!response.ok || !data.access_token || !data.user) {
      throw new Error(classifyAuthResponseError('create an account', proxyUrl, response.status, data.detail, data));
    }
    saveAuthPayload(data.access_token, data.user);
    return data.user;
  }, [saveAuthPayload]);

  const signOut = useCallback(async () => {
    const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (storedToken) {
      await fetch('/api/auth/signout', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${storedToken}`,
        },
      }).catch(() => undefined);
    }
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    writeTokenCookie(null);
    setToken(null);
    setUser(null);
    setError(null);
    setSessionLoading(false);
  }, []);

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
    token,
    user,
    error,
    runtimeConfigDiagnostic: runtimeConfig.diagnostic,
    runtimeConfigSource: runtimeConfig.source,
    isAuthenticated: Boolean(token && user),
    signIn,
    signUp,
    signOut,
    refreshUser,
    createWorkspace,
    selectWorkspace,
    authHeaders,
    setError,
  }), [authHeaders, configLoading, createWorkspace, error, loading, refreshUser, runtimeConfig, selectWorkspace, signIn, signOut, signUp, token, user]);

  return <PilotAuthContext.Provider value={value}>{children}</PilotAuthContext.Provider>;
}

export function usePilotAuth() {
  const value = useContext(PilotAuthContext);
  if (!value) {
    throw new Error('usePilotAuth must be used within PilotAuthProvider');
  }

  return value;
}
