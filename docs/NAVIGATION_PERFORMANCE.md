# Navigation performance and screen simplicity — Guard against Decoda Assets

Audit of how fast Decoda RWA Guard's screens become usable, measured in a real
browser, compared with Decoda Assets' patterns, and what was changed. Every
number below comes from `scripts/capture-navigation-timings.mjs`; the raw
captures are in `artifacts/navigation-timing/`.

## How it was measured

- Local stack: PostgreSQL 16 (disposable database, all migrations), the FastAPI
  API, and a production `next build` / `next start` of `apps/web`. One approved
  Pilot workspace with an MFA-verified owner session and three registered
  assets, created through the real API. No simulator or demo seed was used, and
  nothing was written to any shared environment.
- Chromium via Playwright. Eleven of the twelve sidebar screens (Onboarding,
  a setup workflow, was not captured), 3 loads each:
  - **document load** — a fresh page, static assets warm;
  - **sidebar navigation** — a client-side click from the previous screen (the
    pointer rests 100ms on the link first, the way a person clicks);
  - **steady state** — 120s on Dashboard and on Incidents, counting requests.
- **Added round-trip latency of 150ms** on every request (Chromium network
  emulation). The local API answers in 10–60ms, which hides what each *serial*
  round trip costs a browser that is 100–200ms from the edge. A 0ms run is
  captured too, for reference.
- Columns: **gate** = the auth gate stops showing its loading state
  (`decoda.workspace.ready`); **ready** = last moment a loading indicator was
  visible in `<main>`; **settled** = last `/api/*` response before the network
  went quiet (streams excluded).
- Before = the base commit (its API and its build); after = this branch.

## The critical path before

Every product document load ran three strictly serial round trips before a
screen could ask for its own data:

```
HTML → JS hydrate → GET /api/runtime-config ─RTT→ GET /api/auth/me ‖ /api/auth/csrf ─RTT→ gate opens → screen data ─RTT→
```

`/api/runtime-config` returned a value the server already knew when it rendered
the document. On top of that critical path:

| finding | effect |
|---|---|
| `/api/account/pilot-access` fetched on every load | only an account with **no** membership ever sees that gate |
| `RuntimeSummaryProvider` mounted twice (layout + AppShell) | the outer one had no consumer but ran its own fetch and 90s timer |
| Incidents, Response Actions, Evidence, Threat Monitoring waited for `runtime-status` before fetching their own data | an extra serial round trip on every document load of those screens |
| the summary's `loading` flipped back to `true` on every 90s revalidation | those four screens refetched all their data every 90s, and the global health banner disappeared for each request |
| Dashboard requested `/api/dashboard/executive-summary` twice per load | the live feed's own first snapshot was treated as an update |
| alert stream retried every 3s forever against a 503 | 38 of the Dashboard's 43 requests in two minutes |
| `/api/workspace/retention-policies` had no BFF route | the Settings retention card could only ever report "unavailable" (genuine bug) |
| `/monitoring/sources` opened 4 DB connections per call (5 for `/integrations/control-plane`) and re-checked the session on each | there is no pool: each is a TCP+TLS+auth handshake |

## What changed

**Auth bootstrap**
- The root layout resolves the browser runtime config on the server
  (`getBrowserRuntimeConfig`, shared with `/api/runtime-config` so they cannot
  drift) and hands it to `PilotAuthProvider`. The session restore starts on the
  first effect pass. Without the prop (tests, standalone mounts) the provider
  still fetches the config exactly as before.
- `/api/account/pilot-access` is only read once the session is known to hold no
  membership — the one branch that renders it. Disabled reads as unknown, never
  as access.

**Data loading and polling**
- One `RuntimeSummaryProvider` (AppShell's).
- `loading` now means "no answer for *this workspace* yet". Revalidations replace
  the summary in place; a workspace switch still reads as loading until that
  workspace's own summary settles, so one workspace's status is never shown
  under another's name.
- The four screens gate their reads on the session, not on the runtime summary.
- Those four screens start in a loading state: before, an empty state ("No
  actions match current filters") or a `0` detection count could render before
  the first read had started — in the baseline for the whole runtime-status
  round trip.
- Dashboard ignores the live feed's first completion (its own initial snapshot);
  later polls and SSE alert events still refresh the summary.
- Alert stream reconnects back off 3s → 6s → … → 60s, and reset on a successful
  connection. The live feed's polling fallback carries the screen meanwhile.
- `GET /api/workspace/retention-policies` proxies to the backend route.

**API latency**
- `list_monitoring_sources` composes its listings on one connection
  (`_share_pg_connection`): each listing still authenticates and resolves the
  workspace exactly as on its own; the additive enrichment runs under a
  savepoint so its failure cannot abort the listings' transaction. In-process,
  against the local database, the read model went from ~110ms to ~50ms; the
  saving grows with the network distance to the database.

**Screens (Decoda Assets' focused UX)**
- Header: a breadcrumb trail (group › screen › sub-page) replaces the header
  `<h1>` that duplicated every screen's own `<h1>`. One `<h1>` per screen.
- A skip link, and one `<main>` landmark per screen (the shell's content region
  no longer wraps each screen's `<main>` in a second one).
- Shared screen states (`app/components/screen-states.tsx`): a placeholder-only
  skeleton for the auth gate, and a failure state for a new `(product)/error.tsx`
  boundary — a screen that throws now fails inside the shell instead of taking
  the whole application down. The Dashboard's own error boundary (which used
  utility classes this app does not ship, so it rendered unstyled) uses it too.
- The sidebar card no longer duplicates the header's workspace switcher and
  sign-out; the internal-console link for staff stays.

**Fail-closed fix found by measurement**
- The product layout's Suspense fallback rendered `{children}` — the screen
  itself — *outside* the auth/MFA/membership gate. It only never streamed
  because the gate's loading HTML happened to be under React's 500-byte inline
  limit (React outlines a completed boundary above 500 bytes once a response is
  past 12.8KB, and the shell always is). A slightly larger loading state made
  the server stream a whole screen, as "Guest", ahead of the gate — and tripped
  the CSP on its inline styles (44 violations on Response Actions, 51 on
  Settings, 81 on Evidence). The fallback is now the skeleton, and the skeleton
  is kept under the inline limit (pinned by a test).

## Tried and rejected (measured, not kept)

- **Hover/focus intent prefetch** (`router.prefetch(href, { kind: 'full' })`):
  Next 16 does not reuse a full prefetch for these `force-dynamic` routes — the
  click issued two fresh RSC requests anyway. More requests, no faster.
- **A group-level `(product)/loading.tsx`** (Assets has one): the URL committed
  earlier, but React holds a revealed Suspense fallback for ~300ms, and Guard's
  screens only start fetching after they mount, so the throttle landed on the
  critical path: about one round trip slower per sidebar navigation.

## Results

**Summary at 150ms round-trip latency** (medians of 3 loads per screen):

- Every screen's auth gate opens **116–168ms sooner** (one round trip removed).
- Screen data settles **130–692ms sooner** on every screen; Dashboard
  1369 → 677ms (the duplicate summary request is gone).
- **1–3 fewer requests** per document load.
- Steady state: Dashboard **43 → 10** requests per two minutes (alert-stream
  backoff); Incidents **4 → 1** (no more 90s refetch cascade).
- Sidebar navigation is unchanged within noise (±10–50ms): its cost is the RSC
  round trip and a first-visit chunk load, which this work did not change.
- CSP: zero inline-style violations on Dashboard, Incidents, Response Actions,
  Settings and Evidence, before and after.

Reading notes:

- **Response Actions "ready" is later, on purpose.** Before, the screen rendered
  "No actions match current filters" before its first read had even started,
  so no loading indicator was visible and "ready" (588ms) came *before* the data
  (962ms). It now shows a loading state until the read settles: "ready" (791ms)
  now precedes "settled" (832ms) honestly. Sidebar navigation shows the same
  (0 → 610ms).
- **Settings makes one more request and settles ~50ms later on sidebar
  navigation.** The retention-policies read used to 404 at the BFF in a few
  milliseconds (and, its body never read, did not even appear in Resource
  Timing). It now reaches the backend and returns the workspace's real policy.
- **System Health** renders its data on the server, so on sidebar navigation
  only "URL commit" applies; its other columns read 0.

Before: 2026-10-22T14:24:05.275Z. After: 2026-10-22T14:46:52.905Z. 3 loads per screen, medians in ms; added round-trip latency 150ms.

### Document loads

| screen | auth gate open | ready (no loading indicator) | data settled | API requests |
|---|---:|---:|---:|---:|
| /dashboard | 631 → 500 (-131) | 764 → 646 (-118) | 1369 → 677 (-692) | 9 → 6 (-3) |
| /assets | 634 → 475 (-159) | 832 → 618 (-214) | 861 → 635 (-226) | 8 → 6 (-2) |
| /monitoring-sources | 651 → 522 (-129) | 942 → 667 (-275) | 910 → 713 (-197) | 8 → 6 (-2) |
| /threat | 634 → 479 (-155) | 790 → 617 (-173) | 819 → 643 (-176) | 7 → 5 (-2) |
| /alerts | 659 → 491 (-168) | 794 → 638 (-155) | 814 → 645 (-169) | 7 → 5 (-2) |
| /incidents | 626 → 505 (-121) | 939 → 642 (-296) | 947 → 695 (-252) | 9 → 7 (-2) |
| /response-actions | 633 → 517 (-116) | 588 → 791 (+203) | 962 → 832 (-130) | 10 → 8 (-2) |
| /evidence | 645 → 504 (-141) | 976 → 728 (-248) | 969 → 698 (-270) | 9 → 7 (-2) |
| /integrations | 630 → 481 (-150) | 916 → 691 (-225) | 931 → 691 (-240) | 8 → 6 (-2) |
| /settings | 660 → 523 (-137) | 1038 → 968 (-71) | 1124 → 983 (-142) | 16 → 15 (-1) |
| /system-health | 692 → 547 (-146) | 616 → 466 (-149) | 855 → 707 (-148) | 6 → 4 (-2) |

### Sidebar navigation

| screen | URL commit | ready | data settled | API requests |
|---|---:|---:|---:|---:|
| /assets | 195 → 187 (-8) | 360 → 335 (-25) | 364 → 350 (-14) | 2 → 2 (0) |
| /monitoring-sources | 212 → 200 (-12) | 381 → 352 (-29) | 391 → 357 (-33) | 2 → 2 (0) |
| /threat | 184 → 184 (+0) | 336 → 335 (0) | 345 → 346 (+1) | 1 → 1 (0) |
| /alerts | 180 → 180 (0) | 322 → 326 (+4) | 340 → 335 (-5) | 1 → 1 (0) |
| /incidents | 178 → 180 (+2) | 328 → 319 (-9) | 341 → 333 (-8) | 3 → 3 (0) |
| /response-actions | 173 → 175 (+2) | 0 → 610 (+610) | 627 → 622 (-4) | 4 → 4 (0) |
| /evidence | 171 → 176 (+5) | 611 → 602 (-9) | 618 → 620 (+2) | 3 → 3 (0) |
| /integrations | 176 → 174 (-2) | 373 → 318 (-56) | 376 → 330 (-46) | 2 → 2 (0) |
| /settings | 193 → 186 (-7) | 546 → 596 (+50) | 551 → 598 (+47) | 10 → 11 (+1) |
| /system-health | 172 → 167 (-4) | 0 → 0 (0) | 0 → 0 (0) | 0 → 0 (0) |

### Steady state: 120s on /dashboard

| path | before | after |
|---|---:|---:|
| /api/dashboard/executive-summary | 4 | 4 |
| /api/ops/monitoring/runtime-status | 1 | 1 |
| /api/stream/alerts | 38 | 5 |
| **total** | **43** | **10** |

### Steady state: 120s on /incidents

| path | before | after |
|---|---:|---:|
| /api/alerts | 1 | 0 |
| /api/incidents | 1 | 0 |
| /api/incidents/summary | 1 | 0 |
| /api/ops/monitoring/runtime-status | 1 | 1 |
| **total** | **4** | **1** |

### Document loads with no added latency (reference)

Local round trips are 10–60ms here, and the three loads of a single screen
already spread by up to ~90ms (Alerts after: gate 229–292ms), so differences of
that size are noise in this table (Alerts, System Health). The request counts and
the Dashboard's removed duplicate request are the signal; the 150ms tables above
are the comparison that reflects a deployed browser.

| screen | auth gate open | ready (no loading indicator) | data settled | API requests |
|---|---:|---:|---:|---:|
| /dashboard | 243 → 219 (-23) | 380 → 310 (-70) | 871 → 351 (-520) | 9 → 6 (-3) |
| /assets | 254 → 228 (-26) | 383 → 355 (-28) | 436 → 404 (-32) | 8 → 6 (-2) |
| /monitoring-sources | 240 → 260 (+20) | 523 → 377 (-146) | 523 → 421 (-101) | 8 → 6 (-2) |
| /threat | 266 → 227 (-40) | 357 → 272 (-85) | 467 → 324 (-143) | 7 → 5 (-2) |
| /alerts | 221 → 282 (+61) | 303 → 326 (+24) | 353 → 397 (+44) | 7 → 5 (-2) |
| /incidents | 221 → 227 (+6) | 401 → 320 (-81) | 420 → 387 (-32) | 9 → 7 (-2) |
| /response-actions | 257 → 258 (+2) | 226 → 429 (+203) | 507 → 442 (-64) | 10 → 8 (-2) |
| /evidence | 253 → 254 (+1) | 551 → 445 (-106) | 562 → 453 (-109) | 9 → 7 (-2) |
| /integrations | 277 → 276 (-1) | 548 → 425 (-122) | 558 → 428 (-130) | 8 → 6 (-2) |
| /settings | 277 → 272 (-4) | 679 → 626 (-52) | 686 → 648 (-38) | 16 → 15 (-1) |
| /system-health | 275 → 310 (+35) | 254 → 228 (-26) | 360 → 381 (+22) | 6 → 4 (-2) |

## Decoda Assets comparison

| | Decoda Assets | Guard before | Guard after |
|---|---|---|---|
| Auth gate | server layout checks the session cookie (and AuthKit) and redirects; the shell renders at once | client gate after `runtime-config` → `auth/me` ‖ `csrf` | client gate after `auth/me` ‖ `csrf`; config arrives with the document |
| Screen data | starts on mount, in parallel with `/auth/me` | after the gate; four screens also after `runtime-status` | after the gate |
| Loading vs refreshing | `useAssetsQuery`: skeleton only with no data; a refresh keeps data on screen | runtime summary flipped to loading on every revalidation | loading only until the first answer for the workspace |
| Background polling | none; refresh on demand | 90s summary, 30s live feed, 3s stream retry, 90s screen refetch cascade | 90s summary, 30s live feed (both live-monitoring facts), stream retry backs off to 60s |
| Route boundaries | `loading.tsx` + `error.tsx` for the whole area | Dashboard / System Health only | + `(product)/error.tsx`; group `loading.tsx` measured slower and not kept |
| Header | breadcrumb trail, one `<h1>` per screen | screen name as a second `<h1>` | breadcrumb trail, one `<h1>` |
| Landmarks | skip link, one `<main>` | `<main>` inside `<main>`, no skip link | skip link, one `<main>` per screen |

Guard keeps its client gate on purpose: the screen needs the workspace and the
backend's MFA/membership facts before it renders anything, and those come from
`/auth/me`. Assets' server-side redirect is not a drop-in: Guard's CSRF token is
a double-submit cookie set by `/api/auth/csrf`, which a server component cannot
set, and the gate waits for it so no mutation goes out without it.

## Remaining opportunities (not done here)

1. **One more round trip off every document load**: read `/auth/me` on the
   server and stream it to the provider. It only helps once the CSRF bootstrap
   stops gating the screen — every mount-time mutation would first need an
   audit for `csrfReady` handling.
2. **Sidebar navigation** still pays one RSC round trip plus a first-visit chunk
   load before a screen can fetch. Server-rendered screen data, or a router
   cache that reuses prefetched dynamic payloads, would remove it; both trade
   against data freshness and need their own truthfulness review.
3. **A database connection pool.** Every API request still opens its own
   connection; `_share_pg_connection` only removes the duplicates inside one
   read model.
4. **Settings** reads 11 endpoints for all tabs on open; reading per tab would
   cut the request count, not the latency (they run in parallel).
5. Pre-existing test debt, unchanged by this work: 135 web tests and 57 API tests
   fail on the base commit and on this branch alike (identical sets), and
   `assets-next-action-source.spec.ts` imports `vitest`, which is not installed.

## Reproduce

```bash
make capture-navigation-timings CAPTURE_ARGS="--storage-state auth.json --latency-ms 150 --label after"
node scripts/compare-navigation-timings.mjs \
  artifacts/navigation-timing/before-rtt150/navigation-timings.json \
  artifacts/navigation-timing/after-rtt150/navigation-timings.json
```
