'use client';

import { useId } from 'react';

/**
 * The Decoda shield mark, for product chrome (sidebar, mobile header).
 *
 * Geometry is the repository's own Decoda brand mark — the same shield the
 * public pages in this app draw (`components/home/home-icons.tsx`) — rendered
 * in the teal/cyan identity. Colours come from the fixed `--brand-*` tokens via
 * classes in styles.css, never literals here, so the mark stays identical in
 * Light and Dark and the CSP-safe stylesheet owns every paint.
 *
 * Decorative: the visible "DECODA / RWA Guard" wordmark beside it is the
 * accessible name, so the SVG is aria-hidden.
 */
export function DecodaShieldMark({ className = '' }: { className?: string }) {
  const id = useId().replace(/:/g, '');
  const outer = `dcShieldOuter-${id}`;
  const inner = `dcShieldInner-${id}`;

  return (
    <svg
      className={`brandMark ${className}`.trim()}
      viewBox="0 0 28 28"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={outer} x1="4" y1="2" x2="24" y2="26" gradientUnits="userSpaceOnUse">
          <stop offset="0" className="brandMarkStopMint" />
          <stop offset="1" className="brandMarkStopCyan" />
        </linearGradient>
        <linearGradient id={inner} x1="9" y1="8" x2="19" y2="21" gradientUnits="userSpaceOnUse">
          <stop offset="0" className="brandMarkStopMint" />
          <stop offset="1" className="brandMarkStopTeal" />
        </linearGradient>
      </defs>
      <path
        className="brandMarkOuter"
        d="M14 2.2l9.6 4.3v7.1c0 5.7-4 10.6-9.6 12.2C8.4 24.2 4.4 19.3 4.4 13.6V6.5L14 2.2z"
        stroke={`url(#${outer})`}
        strokeWidth={1.5}
      />
      <path
        className="brandMarkInner"
        d="M14 8.2l5 2.3v3.4c0 3-2.1 5.6-5 6.4-2.9-.8-5-3.4-5-6.4v-3.4l5-2.3z"
        fill={`url(#${inner})`}
      />
      <path
        className="brandMarkCheck"
        d="M11.4 14.2l1.9 1.9 3.5-3.9"
        strokeWidth={1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
