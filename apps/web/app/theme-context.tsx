'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import {
  DEFAULT_THEME_PREFERENCE,
  THEME_STORAGE_KEY,
  normalizeThemePreference,
  resolveTheme,
  type ResolvedTheme,
  type ThemePreference,
} from './theme-preference';

type ThemeContextValue = {
  /** What the user chose: 'system' | 'light' | 'dark'. */
  preference: ThemePreference;
  /** What is actually painted right now: 'light' | 'dark'. */
  resolved: ResolvedTheme;
  setPreference: (next: ThemePreference) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

const DARK_QUERY = '(prefers-color-scheme: dark)';

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(DARK_QUERY).matches;
}

function readStoredPreference(): ThemePreference {
  if (typeof window === 'undefined') return DEFAULT_THEME_PREFERENCE;
  try {
    return normalizeThemePreference(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return DEFAULT_THEME_PREFERENCE;
  }
}

/**
 * Applies the theme to <html>.
 *
 * Deliberately an attribute write rather than React state on <html>: the CSS
 * token layer re-points ~40 custom properties off `data-theme`, so switching
 * themes repaints without re-rendering a single component. Nothing in the
 * product tree subscribes to the resolved theme for layout.
 */
function applyTheme(preference: ThemePreference, resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.setAttribute('data-theme', resolved);
  root.setAttribute('data-theme-preference', preference);
  root.style.colorScheme = resolved;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Both start at the server-rendered default so the first client render
  // matches the server HTML exactly. The effect below reconciles with what
  // the pre-paint script already put on <html>, which is not a visual change
  // — the page is already painted in the stored theme.
  const [preference, setPreferenceState] = useState<ThemePreference>(DEFAULT_THEME_PREFERENCE);
  const [resolved, setResolved] = useState<ResolvedTheme>('light');

  useEffect(() => {
    const stored = readStoredPreference();
    setPreferenceState(stored);
    const next = resolveTheme(stored, systemPrefersDark());
    setResolved(next);
    applyTheme(stored, next);
  }, []);

  // Follow the OS while, and only while, the preference is 'system'.
  useEffect(() => {
    if (preference !== 'system') return;
    if (typeof window === 'undefined' || !window.matchMedia) return;

    const query = window.matchMedia(DARK_QUERY);
    const onChange = () => {
      const next = resolveTheme('system', query.matches);
      setResolved(next);
      applyTheme('system', next);
    };

    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    const normalized = normalizeThemePreference(next);
    setPreferenceState(normalized);
    const resolvedNext = resolveTheme(normalized, systemPrefersDark());
    setResolved(resolvedNext);
    applyTheme(normalized, resolvedNext);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, normalized);
    } catch {
      /* Storage blocked: the choice still applies for this session. */
    }
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    // Never throw: a theme control is chrome, and chrome must not be able to
    // take down an incident screen.
    return { preference: DEFAULT_THEME_PREFERENCE, resolved: 'light', setPreference: () => {} };
  }
  return ctx;
}
