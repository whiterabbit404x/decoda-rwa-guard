/**
 * Theme preference — the single source of truth for Light / Dark / System.
 *
 * The product has no server-side UI-preference store, and the workflow this
 * app exists to serve (asset → target → config → telemetry → detection →
 * alert → incident → action → export) does not involve one. Rather than
 * invent a backend setting and a migration for a display choice, the
 * preference is persisted the way this app already persists UI state
 * (`localStorage`, as the onboarding draft and the history tab do).
 *
 * `resolveTheme` is shared by the pre-paint script and the React provider so
 * the two can never disagree about what "system" means on this device.
 */

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'decoda.theme';

/**
 * Light is the default institutional experience; System and Dark are opt-in.
 *
 * This is the preference used when the analyst has NEVER chosen one — there is
 * nothing under THEME_STORAGE_KEY, or what is there is not a value this control
 * can have written. It must not be 'system': defaulting to the OS setting hands
 * a dark-desktop analyst a dark product they never asked for, and makes the
 * Appearance control claim a choice they never made. The OS is followed only
 * after someone picks System deliberately.
 */
export const DEFAULT_THEME_PREFERENCE: ThemePreference = 'light';

export const THEME_OPTIONS: ReadonlyArray<{ value: ThemePreference; label: string; hint: string }> = [
  { value: 'system', label: 'System', hint: 'Follow this device’s appearance setting' },
  { value: 'light', label: 'Light', hint: 'Light workspace (default)' },
  { value: 'dark', label: 'Dark', hint: 'Low-light workspace' },
];

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'system' || value === 'light' || value === 'dark';
}

/**
 * Read a stored value as a preference.
 *
 * Anything this control cannot have written — a legacy key, a half-written
 * value, a leftover from a development build — is NOT an explicit choice, so it
 * resolves to the default rather than being honoured. That is what keeps a
 * stale `dark` from outliving the hard-coded-dark shell it came from.
 */
export function normalizeThemePreference(value: unknown): ThemePreference {
  return isThemePreference(value) ? value : DEFAULT_THEME_PREFERENCE;
}

/**
 * Resolve a preference to the theme actually painted.
 *
 * `systemPrefersDark` is passed in rather than read here so this stays pure
 * and testable, and so the server never tries to guess a device setting it
 * cannot see.
 */
export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme {
  if (preference === 'light') return 'light';
  if (preference === 'dark') return 'dark';
  return systemPrefersDark ? 'dark' : 'light';
}
