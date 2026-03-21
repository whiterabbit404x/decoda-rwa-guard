'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { resolveApiConfig } from './api-config';

const TOKEN_STORAGE_KEY = 'decoda-pilot-access-token';
const TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24;

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
const API_CONFIG = resolveApiConfig();
const API_URL = API_CONFIG.apiUrl ?? '';
const API_CONFIGURATION_ERROR = API_CONFIG.diagnostic ?? 'Live API URL is not configured.';

export type WorkspaceSummary = {
  id: string;
  name: string;
  slug: string;
};

export type WorkspaceMembership = {
  workspace_id: string;
  role: 'workspace_owner' | 'workspace_admin' | 'workspace_member';
  created_at: string;
  workspace: WorkspaceSummary;
};

export type PilotUser = {
  id: string;
  email: string;
  full_name: string;
  created_at: string;
  updated_at: string;
  last_sign_in_at: string | null;
  current_workspace: WorkspaceSummary | null;
  memberships: WorkspaceMembership[];
};

type PilotAuthContextValue = {
  apiUrl: string;
  liveModeConfigured: boolean;
  loading: boolean;
  token: string | null;
  user: PilotUser | null;
  error: string | null;
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

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

function requireApiUrl() {
  if (!API_URL) {
    throw new Error(API_CONFIGURATION_ERROR);
  }

  return API_URL;
}

export function PilotAuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<PilotUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const liveModeConfigured = Boolean(API_CONFIG.apiUrl);

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
    const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (!storedToken || !API_URL) {
      setToken(null);
      setUser(null);
      setLoading(false);
      return null;
    }

    setToken(storedToken);
    const response = await fetch(`${API_URL}/auth/me`, {
      headers: {
        Authorization: `Bearer ${storedToken}`,
      },
      cache: 'no-store',
    });

    if (!response.ok) {
      const data = await readJson<{ detail?: string }>(response).catch(() => ({ detail: 'Your session expired. Please sign in again.' }));
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      writeTokenCookie(null);
      setToken(null);
      setUser(null);
      setError(data.detail ?? 'Your session expired. Please sign in again.');
      setLoading(false);
      return null;
    }

    const payload = await readJson<{ user: PilotUser }>(response);
    setUser(payload.user);
    setLoading(false);
    return payload.user;
  }, []);

  useEffect(() => {
    void refreshUser().catch((fetchError) => {
      setError(fetchError instanceof Error ? fetchError.message : String(fetchError));
      setLoading(false);
    });
  }, [refreshUser]);

  const saveAuthPayload = useCallback((accessToken: string, nextUser: PilotUser) => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, accessToken);
    writeTokenCookie(accessToken);
    setToken(accessToken);
    setUser(nextUser);
    setError(null);
  }, []);

  const signIn = useCallback(async (payload: { email: string; password: string }) => {
    const response = await fetch(`${requireApiUrl()}/auth/signin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await readJson<{ access_token?: string; user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.access_token || !data.user) {
      throw new Error(data.detail ?? 'Unable to sign in.');
    }
    saveAuthPayload(data.access_token, data.user);
    return data.user;
  }, [saveAuthPayload]);

  const signUp = useCallback(async (payload: { email: string; password: string; full_name: string; workspace_name: string }) => {
    const response = await fetch(`${requireApiUrl()}/auth/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await readJson<{ access_token?: string; user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.access_token || !data.user) {
      throw new Error(data.detail ?? 'Unable to sign up.');
    }
    saveAuthPayload(data.access_token, data.user);
    return data.user;
  }, [saveAuthPayload]);

  const signOut = useCallback(async () => {
    const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (storedToken && API_URL) {
      await fetch(`${API_URL}/auth/signout`, {
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
  }, []);

  const createWorkspace = useCallback(async (name: string) => {
    const response = await fetch(`${requireApiUrl()}/workspaces`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify({ name }),
    });
    const data = await readJson<{ user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.user) {
      throw new Error(data.detail ?? 'Unable to create workspace.');
    }
    setUser(data.user);
    return data.user;
  }, [authHeaders]);

  const selectWorkspace = useCallback(async (workspaceId: string) => {
    const response = await fetch(`${requireApiUrl()}/auth/select-workspace`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    const data = await readJson<{ user?: PilotUser; detail?: string }>(response);
    if (!response.ok || !data.user) {
      throw new Error(data.detail ?? 'Unable to select workspace.');
    }
    setUser(data.user);
    return data.user;
  }, [authHeaders]);

  const value = useMemo<PilotAuthContextValue>(() => ({
    apiUrl: API_URL,
    liveModeConfigured,
    loading,
    token,
    user,
    error,
    isAuthenticated: Boolean(token && user),
    signIn,
    signUp,
    signOut,
    refreshUser,
    createWorkspace,
    selectWorkspace,
    authHeaders,
    setError,
  }), [authHeaders, error, liveModeConfigured, loading, refreshUser, selectWorkspace, signIn, signOut, signUp, token, user, createWorkspace]);

  return <PilotAuthContext.Provider value={value}>{children}</PilotAuthContext.Provider>;
}

export function usePilotAuth() {
  const context = useContext(PilotAuthContext);
  if (!context) {
    throw new Error('usePilotAuth must be used within PilotAuthProvider.');
  }
  return context;
}
