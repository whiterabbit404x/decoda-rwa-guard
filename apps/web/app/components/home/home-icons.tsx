// ─────────────────────────────────────────────────────────────
// Line-icon set for the Decoda homepage.
//
// All icons are inline SVG using `currentColor` so they inherit
// tone from CSS. No inline `style` attributes are used anywhere
// (the production CSP forbids inline styles); sizing is done via
// CSS module rules that target the `svg` element.
// ─────────────────────────────────────────────────────────────

import type { ReactElement } from 'react';

export type IconName =
  | 'telemetry'
  | 'evidenceAi'
  | 'policy'
  | 'export'
  | 'alert'
  | 'incident'
  | 'evidence'
  | 'ai'
  | 'verified'
  | 'onboarding'
  | 'dashboard'
  | 'assetRisk'
  | 'monitoring'
  | 'threat'
  | 'alerts'
  | 'integrations'
  | 'governance'
  | 'health'
  | 'response'
  | 'reserve'
  | 'contract'
  | 'infrastructure'
  | 'insider'
  | 'observe'
  | 'human'
  | 'compliance'
  | 'check'
  | 'arrowRight'
  | 'lock'
  | 'shield'
  | 'institution'
  | 'flag'
  | 'team'
  | 'layers'
  | 'network';

const PATHS: Record<IconName, ReactElement> = {
  telemetry: (
    <>
      <path d="M3 12h3l2 5 4-12 2.5 9L19 12h2" />
    </>
  ),
  evidenceAi: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-4.3-4.3" />
    </>
  ),
  policy: (
    <>
      <path d="M12 3l7 3v5c0 4.2-2.9 7.6-7 8.6C7.9 18.6 5 15.2 5 11V6l7-3z" />
      <path d="M9 11.5l2 2 4-4.5" />
    </>
  ),
  export: (
    <>
      <path d="M12 3v11" />
      <path d="M8.5 7.5L12 4l3.5 3.5" />
      <path d="M5 14v4a2 2 0 002 2h10a2 2 0 002-2v-4" />
    </>
  ),
  alert: (
    <>
      <path d="M12 4l8.5 15H3.5L12 4z" />
      <path d="M12 10v4" />
      <circle cx="12" cy="16.6" r="0.4" fill="currentColor" stroke="none" />
    </>
  ),
  incident: (
    <>
      <path d="M4 7a2 2 0 012-2h4l2 2.2h6a2 2 0 012 2V17a2 2 0 01-2 2H6a2 2 0 01-2-2V7z" />
      <path d="M12 11.5v4M10 13.5h4" />
    </>
  ),
  evidence: (
    <>
      <path d="M7 3h7l4 4v13a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z" />
      <path d="M13 3v5h5" />
      <path d="M9 13h6M9 16.5h4" />
    </>
  ),
  ai: (
    <>
      <path d="M12 3.5l1.7 4.1 4.3 1.6-4.3 1.6L12 15l-1.7-4.2L6 9.2l4.3-1.6L12 3.5z" />
      <path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z" />
    </>
  ),
  verified: (
    <>
      <path d="M12 3l7 3v5c0 4.2-2.9 7.6-7 8.6C7.9 18.6 5 15.2 5 11V6l7-3z" />
      <path d="M9 11.5l2 2 4-4.5" />
    </>
  ),
  onboarding: (
    <>
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="9" r="2.2" />
      <circle cx="8" cy="18" r="2.2" />
      <path d="M7.6 7.4l8.5 1M8 15.8l8.3-4.8" />
    </>
  ),
  dashboard: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.4" />
      <rect x="13" y="4" width="7" height="4.5" rx="1.4" />
      <rect x="13" y="11" width="7" height="9" rx="1.4" />
      <rect x="4" y="13.5" width="7" height="6.5" rx="1.4" />
    </>
  ),
  assetRisk: (
    <>
      <path d="M12 3l7 3v5c0 4.2-2.9 7.6-7 8.6C7.9 18.6 5 15.2 5 11V6l7-3z" />
      <path d="M12 8.5v3.5M12 15.2v.1" />
    </>
  ),
  monitoring: (
    <>
      <rect x="3.5" y="5" width="17" height="6" rx="1.6" />
      <rect x="3.5" y="14" width="17" height="5" rx="1.6" />
      <path d="M7 8h.01M7 16.5h.01" />
    </>
  ),
  threat: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3" />
    </>
  ),
  alerts: (
    <>
      <path d="M6.5 10a5.5 5.5 0 0111 0c0 5 2 6.2 2 6.2H4.5s2-1.2 2-6.2z" />
      <path d="M10 19.5a2 2 0 004 0" />
    </>
  ),
  integrations: (
    <>
      <path d="M8 8V5.5a2.5 2.5 0 015 0V8" />
      <rect x="5" y="8" width="11" height="6" rx="1.6" />
      <path d="M10.5 14v3.5a2.5 2.5 0 005 0V13" />
    </>
  ),
  governance: (
    <>
      <circle cx="12" cy="8" r="3" />
      <path d="M5.5 19a6.5 6.5 0 0113 0" />
      <path d="M18 4.5l1.8.9-1.8.9" />
    </>
  ),
  health: (
    <>
      <path d="M3 12.5h4l2-5 3 9 2-6 1.5 2H21" />
    </>
  ),
  response: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M10 8.5l5 3.5-5 3.5v-7z" fill="currentColor" stroke="none" />
    </>
  ),
  reserve: (
    <>
      <path d="M4 9.5L12 5l8 4.5" />
      <path d="M5.5 9.8v7.2M9.5 9.8v7.2M14.5 9.8v7.2M18.5 9.8v7.2" />
      <path d="M4 19.5h16" />
    </>
  ),
  contract: (
    <>
      <path d="M9 8.5L5.5 12 9 15.5M15 8.5L18.5 12 15 15.5" />
    </>
  ),
  infrastructure: (
    <>
      <rect x="4" y="4.5" width="16" height="5" rx="1.4" />
      <rect x="4" y="11.5" width="16" height="5" rx="1.4" />
      <path d="M7.5 7h.01M7.5 14h.01" />
    </>
  ),
  insider: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 18.5a5.5 5.5 0 0111 0" />
      <path d="M16.5 8.5l2 2 3-3.5" />
    </>
  ),
  observe: (
    <>
      <path d="M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ),
  human: (
    <>
      <circle cx="10" cy="8" r="3" />
      <path d="M4.5 18.5a5.5 5.5 0 0111 0" />
      <path d="M15.5 12.5l1.6 1.6 3-3.2" />
    </>
  ),
  compliance: (
    <>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M9 4.5h6v2.5H9z" />
      <path d="M8.5 12l1.6 1.6 3.4-3.4" />
      <path d="M8.5 17h5" />
    </>
  ),
  check: (
    <>
      <path d="M5 12.5l4 4 10-10.5" />
    </>
  ),
  arrowRight: (
    <>
      <path d="M4 12h15M13 6l6 6-6 6" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="9" rx="2" />
      <path d="M8 10.5V8a4 4 0 018 0v2.5" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3l7 3v5c0 4.2-2.9 7.6-7 8.6C7.9 18.6 5 15.2 5 11V6l7-3z" />
    </>
  ),
  institution: (
    <>
      <path d="M4 9l8-5 8 5" />
      <path d="M6 9.5v8M10 9.5v8M14 9.5v8M18 9.5v8" />
      <path d="M4.5 9.3h15M3 20.5h18" />
    </>
  ),
  flag: (
    <>
      <path d="M6 21V4" />
      <path d="M6 4.5h11l-2.2 3.4L17 11.3H6" />
    </>
  ),
  team: (
    <>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3.5 19a5.6 5.6 0 0111 0" />
      <path d="M16 6.2a3 3 0 010 5.6" />
      <path d="M17.4 13.2a5.4 5.4 0 013.1 4.9" />
    </>
  ),
  layers: (
    <>
      <path d="M12 3.5l8 4.2-8 4.2-8-4.2 8-4.2z" />
      <path d="M4 12l8 4.2 8-4.2" />
      <path d="M4 16.2l8 4.3 8-4.3" />
    </>
  ),
  network: (
    <>
      <circle cx="6" cy="7" r="2.3" />
      <circle cx="18" cy="7" r="2.3" />
      <circle cx="12" cy="17.5" r="2.3" />
      <path d="M8 8.4l3 7.3M16 8.4l-3 7.3M8.3 7h7.4" />
    </>
  ),
};

export function HomeIcon({ name, className }: { name: IconName; className?: string }): ReactElement {
  return (
    <svg
      className={className}
      width={20}
      height={20}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

/** Decoda brand mark — a geometric shield/cube in the brand blue. */
export function DecodaLogo({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width={26}
      height={26}
      viewBox="0 0 28 28"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M14 2.2l9.6 4.3v7.1c0 5.7-4 10.6-9.6 12.2C8.4 24.2 4.4 19.3 4.4 13.6V6.5L14 2.2z"
        fill="rgba(59,130,246,0.16)"
        stroke="#3b82f6"
        strokeWidth={1.5}
      />
      <path d="M14 8.2l5 2.3v3.4c0 3-2.1 5.6-5 6.4-2.9-.8-5-3.4-5-6.4v-3.4l5-2.3z" fill="#3b82f6" />
      <path d="M11.4 14.2l1.9 1.9 3.5-3.9" stroke="#fff" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
