# Decoda RWA Guard — product design system

The authenticated product (everything under `apps/web/app/(product)` plus the app
shell) follows the Decoda Security identity: white and soft-slate surfaces, deep
navy ink and navigation, a teal/cyan interactive accent, a faint geometric grid,
layered soft elevation and restrained, functional motion.

Everything lives in **one stylesheet**, `apps/web/app/styles.css`: the production
CSP (`style-src 'self' 'nonce-…'`) refuses unnonced `<style>` elements and inline
`style` attributes in server-rendered HTML, and the rendered theme/contrast specs
mount the real components against that one file.

Note on inline `style={{…}}` props: authenticated product pages render on the
client, behind `AuthenticatedRoute` (the session gate starts in "loading", so the
server HTML holds only the gate). React applies their style props through the
CSSOM, which the CSP permits — so they do work today. Prefer classes anyway: they
follow the theme tokens, and they keep working if a page is ever server-rendered.
Public and auth pages ARE server-rendered and must not rely on style attributes.

## Layers

1. **Palette (`:root`)** — raw light (`--l-*`) and dark (`--d-*`) values. The only
   place colour literals may appear.
2. **Semantic tokens** — `--bg-*`, `--text-*`, `--border*`, `--accent-*`,
   `--success|warning|danger|info|violet|neutral-{fg,bg,bdr}`, `--shadow-*`,
   `--radius-*`. `[data-theme="dark"]` re-points them; nothing else changes per theme.
3. **Fixed brand tokens** — `--brand-teal`, `--brand-cyan`, `--brand-mint`,
   `--brand-navy`, `--brand-grid-line` and the `--sidebar-*` set. Identical in both
   themes (the sidebar is navy in Light and Dark).
4. **Component rules** — the original per-screen sections.
5. **Decoda brand layer** — shared polish for the shell, cards, buttons,
   inputs, pills, tabs, tables, overlays, empty states, skeletons, the dashboard
   and the onboarding stepper.
6. **Screen layers** — one block per screen at the end of the file, between
   `/* ═══ Screen layer: <name> — begin/end ═══ */` markers (alerts, incidents,
   system-health, onboarding, …). Selectors are scoped to that screen's own
   classes; a shared primitive is never restyled globally from a screen layer.

## Colour rules

| Meaning | Token family | Notes |
| --- | --- | --- |
| Interaction (links, focus, selection, primary action, active nav) | `--accent-*`, `--text-accent`, `--border-hover`, `--ring` | Teal. **Never a status.** The `accent-blue` names are historical. |
| Critical / high / failure | `--danger-*` | |
| Medium / degraded / stale-with-fallback | `--warning-*` | |
| Low severity / informational / investigating | `--info-*` | Blue is reserved for this. `statusVariantFromSeverity('low')` is `info`, so a low-severity alert or incident never wears healthy green. |
| Healthy / verified (only when proven) | `--success-*` | |
| Unknown / no data / not configured / disabled | `--neutral-*` | No data is never shown as safe. |
| Governance / human decision | `--violet-*` | |

Filled teal (`--accent-solid`, teal-700) carries white text at ≥ 5.4:1 in both
themes; the brighter brand teal is used for non-text marks only. The
`theme-system` spec enforces: no hex literals outside the palette, no white
washes, no near-opaque dark literals, and every light token has a dark twin.
Avoid `color-mix()` on text-bearing surfaces — the contrast sweeps cannot parse it.

## Typography

Inter, self-hosted via `next/font/local` (`app/fonts`, SIL OFL) and exposed as
`--font-inter` → `--font-sans`. Metrics and tables use tabular numerals;
identifiers, hashes and addresses use `--font-mono`.

## Motion

Tokens: `--ease-out`, `--ease-in-out`, `--ease-spring`, `--dur-fast` (120ms),
`--dur-base` (200ms), `--dur-slow` (300ms). Keyframes are prefixed `dc`.

- Entrances (page, card, menu, dialog, drawer, chart draw-in) run **once on mount**,
  ≤ 1s, `animation-fill-mode: backwards`, on zero-specificity `:where()` selectors so
  an element's own animation (skeleton shimmer, spinner) always wins.
- A data refresh never replays an entrance; table rows and polled lists are never
  animated.
- Only loading indicators (spinner, shimmer, pulse) may loop.
- `AnimatedNumber` (`app/components/motion.tsx`) never interpolates: the text is
  always the backend value (a tween from 2 to 10 would render 3…9, counts the
  workspace never had). A change only replays a short CSS settle cue on the new
  figure; the first render has no cue, and reduced motion removes it.
- `prefers-reduced-motion: reduce` collapses all durations globally (top of the
  stylesheet).

## Shell

- ≥ 1101px: full navy sidebar, user-collapsible to a 72px icon rail
  (`decoda.sidebar` in localStorage; labels stay in the DOM for assistive tech and
  appear as tooltips).
- 768–1100px: icon rail automatically.
- < 768px: off-canvas drawer opened from the header menu button; Escape, the scrim
  and the close button dismiss it; focus moves in, Tab wraps inside it, the page
  behind is `inert`, and focus returns to the menu button. Leaving the phone
  breakpoint closes it and releases the scroll lock.
- The header bell opens `/alerts`; it never shows a count of its own.
- Brand mark: `DecodaShieldMark` (`app/components/brand-mark.tsx`), the repository's
  Decoda shield geometry in brand tokens. Replace its paths with the canonical
  asset from decodasecurity.com when that file is added to the repository.

## Adding UI

- Reach for `app/components/ui-primitives.tsx` first (SurfaceCard, MetricTile,
  StatusPill, TableShell, EmptyStateBlocker, TabStrip, Button, Select).
- Use classes rather than `style={{…}}` (see the note at the top on why).
- Every visible control must do something; disabled and loading states must look
  different (`.btn:disabled` is flat and inert, `.btn[data-loading]` keeps colour
  and shows a spinner).
- Add or update a spec in `apps/web/tests` for any behaviour change.
