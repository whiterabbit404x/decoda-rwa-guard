# Screen simplification — Guard in the Decoda Assets idiom

What changed on the product screens, why, and the measured before/after. The goal
was the Decoda Assets feel: navigation that answers the click at once, one simple
page hierarchy, fewer technical panels on the first screen, and one clear primary
action per screen — without weakening a security control or a truthfulness rule.

## How it was measured

- A disposable local stack: PostgreSQL 16 with every migration, the FastAPI API,
  and production `next build` / `next start` of `apps/web` — the base commit's
  build and this branch's, side by side, against the same API and database.
- One Pilot workspace created through the real production path: a staff account
  approved a Pilot request, the invited owner signed up, verified the address,
  enrolled TOTP MFA and accepted the invitation. Three assets and three
  monitoring targets were registered through `POST /assets` and `POST /targets`.
  Five alerts and one incident were raised through the signed Decoda Assets
  intake (`POST /api/integrations/decoda/events`, HMAC v1), so they carry the
  product's own "Testnet product event (synthetic data)" label. No simulator or
  demo seed was used. The one direct database write was
  `organizations.platform_organization_id`, which the shared Decoda identity sets
  in production and the local password sign-in does not.
- Screenshots: Chromium at 1440×900 (and 390×844 for the phone check), signed in
  with the owner's MFA-verified session cookie.
- Timings: `scripts/capture-navigation-timings.mjs`, 11 sidebar screens × 3
  loads, sidebar navigation × 3 passes, 120s steady state on Dashboard and
  Incidents, with **150ms added round-trip latency** on every request (Chromium
  network emulation), plus a 0ms reference run. A new column, **click feedback**,
  is the first animation frame after a sidebar click in which the clicked item
  reads as selected.

## What changed

### The shell (every screen)

| before | after |
|---|---|
| Two permanent rows above every screen's own content: a `SETUP REQUIRED` strip, then ten runtime readings (Monitoring, Freshness, Confidence, Telemetry, Heartbeat, Poll, Next action, Workers, Limitation…) | One **Monitoring** chip in the header stating the canonical verdict in words (`Setup required`, `Limited coverage`, `Live`, `Offline`), toned by `runtimeFieldTone` so it can never read green without proof. Click → the same two strips, unchanged, below the header; Escape closes and returns focus. |
| An `OFFLINE` verdict strip (runtime or database unreachable) | **Unchanged and still permanent**: a critical state is never folded behind the chip (`OfflineVerdictStrip`). |
| Sidebar click: nothing visible happens until the route commits (~180–200ms at 150ms RTT) | The clicked item highlights in the same frame, a bar runs under the header and the outgoing screen steps back; `aria-current` keeps naming the page actually shown. |
| Only Dashboard was prefetched; every other click paid an RSC round trip | Every sidebar screen whose page segment carries no data is fully prefetched; System Health (server-rendered backend data) is excluded (`SERVER_DATA_ROUTES`). |

### Page hierarchy (shared)

`PageHeader` (one `<h1>`, one line of purpose, actions with the primary one last)
and `StatRow` (one framed row of key figures) in `app/components/page-header.tsx`;
technical depth behind native `<details>` (`.pageDisclosure`). See
`docs/GUARD_DESIGN_SYSTEM.md` → Page hierarchy.

### Screens

| screen | before | after |
|---|---|---|
| **Dashboard** | 3 status pills, a telemetry banner, 4 posture cards, executive brief, 5 metric cards, an empty risk-trend panel, an AI co-pilot sidebar, recent alerts, 4 more metric cards (≈20 panels) | Header with **one primary action — the backend's own top recommended focus** (here "Review open incidents"); one monitoring status card (verdict, reason, telemetry/heartbeat/ingestion/live-updates/system-health facts, 24h activity, *Set up monitoring* / *Diagnose*); 4 key figures (open incidents, active alerts, monitored assets, risk score); Recent Alerts beside the Executive Brief with its recommended focus. Risk drivers, health insights, coverage counts, uptime and the trend's absence moved into the full-summary drawer. The trend chart renders only when real history exists. |
| **Monitoring Sources** | 5 technical KPI cards, a "page updates" chip and a reconnecting banner, a 13-column table that scrolled sideways, 7 filter controls, an agent rail | Header (Run Health Check, **Add Source**), live-update state as one meta line, 4 figures, a **7-column** table (target, network, status, health, last event, routing, actions) that fits; provider, role, P95 latency, block lag, error rate, heartbeat and poll ages are in each source's Details drawer; provider/routing/sort filters under *More filters*; the Source optimization agent collapsed below. |
| **Alerts** | Table squeezed to ~75% by the AI triage sidebar; long titles wrapped to 6–7 lines | Triage agent is a compact bar above the queue with **Run Triage** as the primary action; the alert table spans the full width (same six columns, same truthful *Unclustered*, same evidence labels). |
| **Incidents** | 4 KPI cards, an assignee-user-ID text box in the main filter bar, the first incident's case file auto-opened so the queue table was cut off (`ACTI…`, `Vie…`) | Header with **Create Incident**; 4 figures in one row (still `—` + reason when a count is unknown); assignee filter under *More filters*; the queue opens at full width and a case file opens on row click, with a close control. Deep links still pre-open their incident. |
| **Assets** | Table beside a tall AI risk-assessor rail | Header with **Add Asset**; the registry at full width; the assessor (still mounted, same filters it drives) collapsed below. |
| **Threat Monitoring** | 4 icon KPI cards | The same KPIs as one row of figures (CSS only — the threat truth model is untouched). |
| **Response Actions** | Filters above the figures; a permanently **disabled** "Recommend Action" primary button | Header with a working primary action — *Recommend from an incident* (recommendations start from an incident's case file); figures, then filters. |
| **Evidence & Audit** | 4 metric cards | One row of figures (same backend values, same capped/“none verified” semantics). |
| **Integrations** | 5 metric cards, agent rail beside the providers | Header (Run Health Check, **Add Provider**), one row of figures, Decoda products, providers at full width, Integration gateway agent collapsed below. |
| **System Health** | 6,600px: runtime summary panel, deployment SHAs, verdict, reliability agent, summary cards, live chain monitoring, operational overview, status/reliability panels, timeline, provider cards, API docs | Verdict first (hero), reliability agent and component health; *Monitoring runtime detail*, *Health timeline* and *Deployment and API reference* collapsed below. Server-rendered with classes and `<details>` only — no inline style, so the production CSP is unaffected. |

## Security controls and truthfulness — unchanged

- **Authentication, MFA, membership and CSRF**: no change to `proxy.ts`, the
  product layout's server redirect, `AuthenticatedRoute`, the MFA gate, the CSRF
  bootstrap or any BFF route. No backend code changed; no query was added or
  altered, so every read stays workspace-scoped exactly as before.
- **Prefetch**: a prefetch is an authenticated GET through the same proxy and
  layout redirect as a click. Only page segments that carry no data are
  prefetched (pinned by `shell-status-and-navigation-render.spec.ts`, which fails
  if any prefetched page starts fetching on the server); every screen still reads
  its data after it mounts, through the session-scoped BFF, so a prefetched
  screen can never show data older than its own fresh read.
- **CSP**: no inline `style` in server-rendered markup (System Health uses classes
  and native `<details>`). A sweep of all eleven screens under the production
  `next start` CSP, with every disclosure and the header status panel opened,
  logged **0 CSP violations and 0 page errors** on the base build and on this
  branch alike.
- **Fail-closed status**: the header chip's word comes from the same derivation
  as the strip's "Monitoring" field, its colour from `runtimeFieldTone` (an
  unmapped or absent reading is grey; only a proven-live verdict is green); an
  unreachable runtime or database reads **Offline** in red and keeps its verdict
  strip on screen. Heartbeat, poll and telemetry remain three separate readings.
  The Dashboard status card reads `monitoring_state` (fail-closed to offline),
  keeps telemetry, heartbeat, ingestion and the browser's own connection as
  separate facts, and keeps the zero-alert caveat. Figures are never toned
  green; an unknown incident count still reads `—` with its reason.
- **Simulator / product events**: the testnet product-event labels on alerts and
  the simulator/live distinctions on Monitoring Sources render exactly as before.

## Results

### What a person sees on arrival (1440×900, same data)

Measured by the screenshot run (`artifacts/screen-redesign/layout-measures.json`):
**title top** is where the screen's `<h1>` starts (everything above it is shell);
**page height** is the full scroll height; **text in <main>** is the screen's rendered text,
a proxy for how much is on it.

| screen | title top (px) | page height (px) | text in &lt;main&gt; (chars) |
|---|---:|---:|---:|
| /dashboard | 187 → 83 | 1702 → 1488 | 2812 → 2055 |
| /assets | 187 → 83 | 1229 → 900 | 1692 → 1022 |
| /monitoring-sources | 187 → 83 | 1406 → 900 | 2531 → 1144 |
| /threat | 187 → 83 | 1442 → 1321 | 1694 → 1694 |
| /alerts | 213 → 83 | 1911 → 1265 | 1923 → 1901 |
| /incidents | 213 → 83 | 1492 → 900 | 1516 → 639 |
| /response-actions | 203 → 83 | 900 → 900 | 650 → 651 |
| /evidence | 203 → 99 | 900 → 900 | 429 → 418 |
| /integrations | 187 → 83 | 1266 → 900 | 1572 → 1133 |
| /settings | 229 → 125 | 1385 → 1281 | 1626 → 1626 |
| /system-health | 1123 → 83 | 6458 → 2583 | 9885 → 4282 |

Every screen's own title moved up 104–130px (System Health: from 1,123px — below a
runtime panel — to 83px), four more screens fit one viewport (Assets, Monitoring
Sources, Incidents, Integrations), and the first screen
holds more of the screen's own content and less shell. Dashboard's height includes
its risk-trend chart: the workspace had two daily snapshots by the final capture, so
the chart now draws from real history (labelled *Partial data*); before that day it
was omitted rather than shown empty.

Screenshots, before | after, in `artifacts/screen-redesign/`:
`first-screen/<screen>.jpg` (the 1440×900 viewport), `full-page/<screen>.jpg`, and
`phone/<screen>.jpg` (390×844, after). At 390px no screen scrolls sideways (before:
System Health overflowed by 17px).

### Navigation timings, 150ms added round-trip latency (medians of 3)

Before: 2026-10-23T03:01:32.235Z. After: 2026-10-23T03:08:01.826Z. 3 loads per screen, medians in ms; added round-trip latency 150ms.

#### Document loads

| screen | auth gate open | ready (no loading indicator) | data settled | API requests |
|---|---:|---:|---:|---:|
| /dashboard | 480 → 508 (+28) | 617 → 638 (+21) | 642 → 664 (+22) | 6 → 6 (0) |
| /assets | 505 → 492 (-12) | 669 → 643 (-27) | 711 → 662 (-49) | 6 → 6 (0) |
| /monitoring-sources | 531 → 523 (-8) | 667 → 665 (-2) | 740 → 706 (-34) | 6 → 6 (0) |
| /threat | 484 → 490 (+6) | 641 → 642 (+0) | 652 → 647 (-5) | 5 → 5 (0) |
| /alerts | 497 → 488 (-9) | 646 → 641 (-5) | 657 → 654 (-3) | 5 → 5 (0) |
| /incidents | 515 → 499 (-17) | 893 → 671 (-223) | 895 → 709 (-186) | 10 → 7 (-3) |
| /response-actions | 554 → 518 (-36) | 769 → 690 (-79) | 777 → 676 (-101) | 8 → 8 (0) |
| /evidence | 521 → 509 (-11) | 710 → 679 (-31) | 694 → 667 (-27) | 7 → 7 (0) |
| /integrations | 510 → 476 (-34) | 717 → 643 (-73) | 704 → 647 (-57) | 6 → 6 (0) |
| /settings | 539 → 518 (-21) | 898 → 848 (-50) | 996 → 974 (-21) | 15 → 15 (0) |
| /system-health | 528 → 519 (-9) | 443 → 450 (+7) | 686 → 696 (+10) | 4 → 4 (0) |

#### Sidebar navigation

| screen | click feedback | URL commit | ready | data settled | API requests |
|---|---:|---:|---:|---:|---:|
| /assets | 188 → 14 (-174) | 193 → 32 (-161) | 359 → 186 (-173) | 362 → 193 (-169) | 2 → 2 (0) |
| /monitoring-sources | 202 → 26 (-176) | 206 → 34 (-172) | 363 → 188 (-175) | 369 → 195 (-174) | 2 → 2 (0) |
| /threat | 190 → 11 (-179) | 195 → 33 (-162) | 348 → 181 (-167) | 355 → 183 (-171) | 1 → 1 (0) |
| /alerts | 184 → 17 (-166) | 186 → 20 (-166) | 331 → 169 (-163) | 346 → 180 (-166) | 1 → 1 (0) |
| /incidents | 184 → 16 (-168) | 187 → 19 (-168) | 516 → 173 (-342) | 524 → 180 (-344) | 6 → 3 (-3) |
| /response-actions | 180 → 22 (-157) | 178 → 30 (-147) | 618 → 194 (-424) | 629 → 199 (-430) | 4 → 4 (0) |
| /evidence | 177 → 14 (-162) | 171 → 36 (-135) | 611 → 197 (-415) | 624 → 199 (-424) | 3 → 3 (0) |
| /integrations | 179 → 17 (-162) | 179 → 24 (-155) | 337 → 170 (-167) | 335 → 180 (-155) | 2 → 2 (0) |
| /settings | 192 → 11 (-181) | 195 → 38 (-156) | 541 → 370 (-171) | 552 → 376 (-177) | 11 → 11 (0) |
| /system-health | 216 → 11 (-205) | 173 → 174 (+1) | 0 → 179 (+179) | 0 → 0 (0) | 0 → 0 (0) |

#### Steady state: 120s on /dashboard

| path | before | after |
|---|---:|---:|
| /api/dashboard/executive-summary | 4 | 4 |
| /api/ops/monitoring/runtime-status | 1 | 1 |
| /api/stream/alerts | 5 | 5 |
| **total** | **10** | **10** |

#### Steady state: 120s on /incidents

| path | before | after |
|---|---:|---:|
| /api/ops/monitoring/runtime-status | 1 | 1 |
| **total** | **1** | **1** |

Reading notes:

- **Sidebar navigation is where the screens now feel immediate.** The click
  registers in 11–26ms (one or two frames) instead of 177–216ms, the route commits
  in 19–38ms instead of 171–206ms, and each screen is ready about one round trip
  after the click (169–199ms) instead of two or three (331–618ms). Settings stays
  slower (370ms): it reads 11 endpoints on open, unchanged here.
- **Incidents** loads 3 fewer requests: the first incident's case file (evidence,
  response actions, investigation) is no longer fetched for a panel nobody opened.
- **System Health** is not prefetched (server-rendered backend data), so its commit
  is unchanged (173 → 174). Its sidebar "ready" equals that commit; before it read
  0 because no loading indicator was ever sampled (see `NAVIGATION_PERFORMANCE.md`).
- **Document loads** are unchanged within the run-to-run spread (±30ms at this
  scale) except where a screen now does less: Incidents −186ms settled, Response
  Actions −101ms, Integrations −57ms.
- Steady state is unchanged: the redesign adds no polling.

### Reference: 0ms added latency

Local round trips are 10–60ms, so differences under ~50ms are noise. Sidebar
navigation still gains most on the screens that do less on arrival (Response
Actions ready 462 → 169ms, Evidence 415 → 136ms, Incidents 298 → 114ms) and
click feedback halves (28–58ms → 9–30ms). Full tables:
`artifacts/navigation-timing/redesign-{before,after}-rtt0/`.

## Tried and measured

- **Eager sidebar prefetch** (every link prefetched at hydration). Sidebar
  navigation was as fast as the final version, but document loads got slower:
  data settled +47ms to +103ms on six screens. Ten RSC payloads competed with the
  screen's own first reads for the browser's six HTTP/1.1 connections. The final
  version arms prefetch 1.2s after the shell mounts — after every screen's first
  reads at 150ms RTT — or as soon as the pointer or keyboard focus reaches the
  sidebar. With that, the first click after a load is already prefetched
  (/assets: 27ms commit) and document loads no longer regress.
- **Prefetch arming at 2s.** The first sidebar click within 2s of a load still
  paid the full round trip (196ms); 1.2s does not.

## Remaining risks and follow-ups

- **Prefetch cost on the server**: up to ten extra RSC renders per session after
  the first 1.2s (each is the product layout's session check plus a page segment
  that renders no data). In production (HTTP/2) there is no six-connection limit,
  so the document-load contention measured here should be smaller there.
- **A new data-fetching server page** added to the sidebar would be prefetched
  unless it is added to `SERVER_DATA_ROUTES`; the render spec fails if any
  prefetched page starts fetching on the server, so the omission is caught.
- **Settings** still opens on 11 endpoint reads; per-tab loading would cut it.
- The timings were captured on the final build except for three later
  presentation-only edits (a keyframe renamed, the Dashboard status card's
  "Not reported" / heartbeat copy, System Health leading with Deployment Version
  when its endpoint is unreachable); none changes what loads or when.
- Pre-existing test debt is unchanged: 134 web tests fail on the base commit and
  on this branch alike (identical sets); `assets-next-action-source.spec.ts`
  imports `vitest`, which is not installed.

## Reproduce

```bash
make capture-navigation-timings CAPTURE_ARGS="--storage-state auth.json --latency-ms 150 --label after"
node scripts/compare-navigation-timings.mjs \
  artifacts/navigation-timing/redesign-before-rtt150/navigation-timings.json \
  artifacts/navigation-timing/redesign-after-rtt150/navigation-timings.json
```
