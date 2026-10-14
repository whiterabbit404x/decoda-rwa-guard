// ─────────────────────────────────────────────────────────────
// Decoda line icons for the public website.
//
// The company website's icon family (decoda-website
// components/company/icons.tsx): one 24px grid, 1.7px rounded strokes,
// currentColor. The product-area glyphs at the end are drawn in the same
// style. Every icon is decorative (aria-hidden) — the text beside it
// carries the meaning. No inline style attributes (the production CSP
// forbids them); size comes from the `size` prop or from CSS.
// ─────────────────────────────────────────────────────────────

import type { ReactElement } from 'react';

const PATHS = {
  shield: (
    <>
      <path d="M12 3 20 6.5v5C20 16.5 16.5 20.5 12 22 7.5 20.5 4 16.5 4 11.5v-5L12 3Z" />
      <path d="m8.8 12.2 2.2 2.2 4.4-4.6" />
    </>
  ),
  radar: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 12 18 6" />
      <circle cx="15.5" cy="9.5" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  observe: (
    <>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="2.8" />
    </>
  ),
  detect: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3" />
    </>
  ),
  investigate: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m20 20-4.6-4.6" />
    </>
  ),
  review: (
    <>
      <path d="M9 4.5H6.5A1.5 1.5 0 0 0 5 6v13.5A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H15" />
      <rect x="9" y="3" width="6" height="3.2" rx="1" />
      <path d="m9 13.5 2 2 4-4.2" />
    </>
  ),
  respond: (
    <>
      <path d="M12 3 20 6.5v5C20 16.5 16.5 20.5 12 22 7.5 20.5 4 16.5 4 11.5v-5L12 3Z" />
      <path d="m12.7 8-3 4.4h2.3l-.7 3.6 3-4.4h-2.3l.7-3.6Z" />
    </>
  ),
  evidence: (
    <>
      <path d="M8 3.5h6.5L18.5 7.5V19a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 19V5A1.5 1.5 0 0 1 8 3.5Z" />
      <path d="M14 3.5V8h4.5" />
      <path d="m9.5 14 1.6 1.6 3-3.2" />
    </>
  ),
  alert: (
    <>
      <path d="M10.3 4.6 3.2 17a2 2 0 0 0 1.7 3h14.2a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9.5v4M12 16.8v.2" />
    </>
  ),
  spark: (
    <>
      <path d="M12 3.5c.5 3.6 1.9 5 5.5 5.5-3.6.5-5 1.9-5.5 5.5-.5-3.6-1.9-5-5.5-5.5 3.6-.5 5-1.9 5.5-5.5Z" />
      <path d="M18.5 14.5c.25 1.6.9 2.25 2.5 2.5-1.6.25-2.25.9-2.5 2.5-.25-1.6-.9-2.25-2.5-2.5 1.6-.25 2.25-.9 2.5-2.5Z" />
    </>
  ),
  policy: (
    <>
      <path d="M12 3.5v17M6 20.5h12" />
      <path d="M5 7.5h14" />
      <path d="M5 7.5 2.8 13a2.6 2.6 0 0 0 4.4 0L5 7.5ZM19 7.5 16.8 13a2.6 2.6 0 0 0 4.4 0L19 7.5Z" />
    </>
  ),
  approval: (
    <>
      <circle cx="8.5" cy="8" r="3" />
      <path d="M3 19.5a5.5 5.5 0 0 1 9.4-3.9" />
      <path d="m14.5 17 2 2 4-4.2" />
    </>
  ),
  hash: <path d="M9.5 3.5 7.5 20.5M16.5 3.5l-2 17M4.5 9h16M3.5 15h16" />,
  userKey: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 9.8-5.6" />
      <circle cx="17" cy="15" r="2.4" />
      <path d="m18.7 16.7 2.8 2.8M20.2 18.2l-1 1" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
  transfer: (
    <>
      <path d="M4 8.5h14.5M15 5l3.5 3.5L15 12" />
      <path d="M20 15.5H5.5M9 12l-3.5 3.5L9 19" />
    </>
  ),
  roles: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <path d="M16 5.2a3.2 3.2 0 0 1 0 6M17.5 20a5.5 5.5 0 0 0-3-4.9" />
    </>
  ),
  fingerprint: (
    <>
      <path d="M5 10.5a7 7 0 0 1 14 0v1.5" />
      <path d="M8 18.5c.8-1.8 1-3.5 1-6a3 3 0 0 1 6 0c0 2.8-.4 5.2-1.4 7.5" />
      <path d="M12 12.5c0 3.5-.6 6-2 8.5M18.5 15.5c-.2 1.5-.5 2.8-1 4" />
    </>
  ),
  lock: (
    <>
      <rect x="4.5" y="10.5" width="15" height="10" rx="2.2" />
      <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
      <path d="M12 14.5v2.5" />
    </>
  ),
  eyeLock: (
    <>
      <path d="M2.5 11S6 4.5 12 4.5 21.5 11 21.5 11" />
      <path d="M2.5 11s1.3 2.4 3.8 4.3" />
      <circle cx="12" cy="11" r="2.6" />
      <rect x="13.5" y="16" width="7" height="5" rx="1.2" />
      <path d="M15 16v-1.2a2 2 0 0 1 4 0V16" />
    </>
  ),
  layers: (
    <>
      <path d="M12 3.5 21 8l-9 4.5L3 8l9-4.5Z" />
      <path d="m3 12 9 4.5 9-4.5" />
      <path d="m3 16 9 4.5 9-4.5" />
    </>
  ),
  token: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 6.5v11" />
      <path d="M15 8.8c-.6-.9-1.7-1.3-3-1.3-1.8 0-3 .9-3 2.2 0 3.3 6.2 1.8 6.2 4.8 0 1.4-1.4 2.3-3.2 2.3-1.4 0-2.6-.5-3.2-1.5" />
    </>
  ),
  vault: (
    <>
      <rect x="3.5" y="4" width="17" height="15" rx="2.5" />
      <circle cx="12" cy="11.5" r="3.6" />
      <path d="M12 7.9v1.2M12 13.9v1.2M8.4 11.5h1.2M14.4 11.5h1.2" />
      <path d="M6.5 19v1.5M17.5 19v1.5" />
    </>
  ),
  workflow: (
    <>
      <rect x="3" y="4" width="6" height="5" rx="1.4" />
      <rect x="15" y="4" width="6" height="5" rx="1.4" />
      <rect x="9" y="15" width="6" height="5" rx="1.4" />
      <path d="M9 6.5h6M6 9v3.5h6V15M18 9v3.5h-6" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3.5v4M15 3.5v4" />
      <path d="M6.5 7.5h11V11a5.5 5.5 0 0 1-11 0V7.5Z" />
      <path d="M12 16.5v4" />
    </>
  ),
  network: (
    <>
      <circle cx="12" cy="5" r="2.2" />
      <circle cx="5.5" cy="18" r="2.2" />
      <circle cx="18.5" cy="18" r="2.2" />
      <path d="M12 7.2v3.3M11 10.5 6.6 16M13 10.5 17.4 16M7.7 18h8.6" />
    </>
  ),
  building: (
    <>
      <path d="M3.5 8.5 12 4l8.5 4.5" />
      <path d="M5 8.5v9M9.5 8.5v9M14.5 8.5v9M19 8.5v9" />
      <path d="M3.5 20.5h17" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
    </>
  ),
  arrowRight: <path d="M5 12h14M13 6l6 6-6 6" />,
  arrowUpRight: <path d="M7 17 17 7M8.5 7H17v8.5" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  check: <path d="m5 12.5 4.5 4.5L19 7" />,
  checkCircle: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12 2.4 2.4L15.8 9.5" />
    </>
  ),
  minus: <path d="M6 12h12" />,
  plus: <path d="M12 5v14M5 12h14" />,
  question: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.7M12 16.8v.2" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 19.5c1.2-3.2 3.9-5 7-5s5.8 1.8 7 5" />
    </>
  ),
  external: (
    <>
      <path d="M14 5h5v5M19 5l-7.5 7.5" />
      <path d="M18 14.5V18a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18V7a1.5 1.5 0 0 1 1.5-1.5H9" />
    </>
  ),
  mail: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2.2" />
      <path d="m3.8 6.5 8.2 6.2 8.2-6.2" />
    </>
  ),
  /* --- Product areas (same grid and stroke) --- */
  dashboard: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.6" />
      <rect x="13.5" y="3.5" width="7" height="4.5" rx="1.6" />
      <rect x="13.5" y="11" width="7" height="9.5" rx="1.6" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.6" />
    </>
  ),
  assets: (
    <>
      <ellipse cx="12" cy="6.5" rx="7" ry="3" />
      <path d="M5 6.5v5c0 1.66 3.13 3 7 3s7-1.34 7-3v-5" />
      <path d="M5 11.5v5c0 1.66 3.13 3 7 3s7-1.34 7-3v-5" />
    </>
  ),
  sources: (
    <>
      <path d="M12 12v8.5" />
      <circle cx="12" cy="10" r="2" />
      <path d="M7.8 14.2a6 6 0 0 1 0-8.4M16.2 5.8a6 6 0 0 1 0 8.4" />
      <path d="M5 17a10 10 0 0 1 0-14M19 3a10 10 0 0 1 0 14" />
    </>
  ),
  activity: <path d="M3 12h3.5l2.5-6 4 12 2.5-6H21" />,
  bell: (
    <>
      <path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15l1.5-2Z" />
      <path d="M10 20.5a2.2 2.2 0 0 0 4 0" />
    </>
  ),
  incident: (
    <>
      <path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2h8A1.5 1.5 0 0 1 20.5 9v9a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18V7Z" />
      <path d="M12 11v3.6M12 16.8v.1" />
    </>
  ),
  health: (
    <>
      <rect x="3" y="4.5" width="18" height="13" rx="2" />
      <path d="M6.5 11h2.5l1.5-3 2.5 6 1.5-3h3" />
      <path d="M9 21h6" />
    </>
  ),
} satisfies Record<string, ReactElement>;

export type IconName = keyof typeof PATHS;

export function HomeIcon({
  name,
  size = 20,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}): ReactElement {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
