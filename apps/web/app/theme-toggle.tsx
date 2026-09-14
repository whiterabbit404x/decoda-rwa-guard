'use client';

import { THEME_OPTIONS, type ThemePreference } from './theme-preference';
import { useTheme } from './theme-context';

/**
 * The three-way appearance control (System / Light / Dark).
 *
 * One component backs both the user-menu shortcut and Settings › Appearance,
 * so the two can never drift and both write the same stored preference.
 *
 * Exposed as a radiogroup rather than three buttons so a screen reader
 * announces it as one setting with a current value, and so arrow keys move
 * between options the way a native radio group does.
 */
export default function ThemeToggle({
  variant = 'menu',
  labelledBy,
}: {
  variant?: 'menu' | 'settings';
  labelledBy?: string;
}) {
  const { preference, setPreference } = useTheme();

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const keys = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const index = THEME_OPTIONS.findIndex((option) => option.value === preference);
    const delta = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1;
    const next = THEME_OPTIONS[(index + delta + THEME_OPTIONS.length) % THEME_OPTIONS.length];
    setPreference(next.value);
  }

  return (
    <div
      className={`themeToggle${variant === 'settings' ? ' themeToggle--settings' : ''}`}
      role="radiogroup"
      aria-label={labelledBy ? undefined : 'Appearance'}
      aria-labelledby={labelledBy}
      onKeyDown={onKeyDown}
    >
      {THEME_OPTIONS.map((option) => {
        const checked = option.value === preference;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            className="themeToggleOption"
            title={option.hint}
            onClick={() => setPreference(option.value as ThemePreference)}
          >
            <ThemeIcon value={option.value} />
            <span>{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function ThemeIcon({ value }: { value: ThemePreference }) {
  const common = {
    width: 13,
    height: 13,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };

  if (value === 'light') {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  if (value === 'dark') {
    return (
      <svg {...common}>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <rect x="2" y="4" width="20" height="13" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}
