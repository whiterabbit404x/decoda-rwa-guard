# Navigation timings — before-rtt0

Captured 2026-10-22T14:26:24.567Z against http://127.0.0.1:3000; 3 load(s) per route, medians shown; added round-trip latency 0ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 13.8 | 88 | 242.6 | 379.5 | 871.1 | 9 | 1 |
| /assets | 13.9 | 96 | 254 | 383.1 | 436.1 | 8 | 0 |
| /monitoring-sources | 12.5 | 96 | 240.1 | 522.5 | 522.7 | 8 | 0 |
| /threat | 13.7 | 96 | 266.4 | 357 | 466.7 | 7 | 0 |
| /alerts | 11.3 | 84 | 220.7 | 302.5 | 353 | 7 | 0 |
| /incidents | 15 | 88 | 220.7 | 400.5 | 419.5 | 9 | 0 |
| /response-actions | 12.8 | 100 | 256.6 | 226.2 | 506.7 | 10 | 0 |
| /evidence | 13.2 | 96 | 253.2 | 550.6 | 561.8 | 9 | 0 |
| /integrations | 15.2 | 88 | 277.2 | 547.7 | 557.7 | 8 | 0 |
| /settings | 15.8 | 96 | 276.5 | 678.5 | 685.8 | 16 | 0 |
| /system-health | 17.4 | 124 | 274.7 | 253.7 | 359.7 | 6 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|
| /assets | 43.8 | 156.6 | 155.7 | 2 | 0 |
| /monitoring-sources | 61.4 | 216.7 | 228.1 | 2 | 0 |
| /threat | 38.5 | 94.2 | 106.1 | 1 | 0 |
| /alerts | 29.1 | 85.5 | 87.7 | 1 | 0 |
| /incidents | 31.2 | 107.9 | 151.2 | 3 | 0 |
| /response-actions | 24.8 | 0 | 473.9 | 4 | 0 |
| /evidence | 26 | 433.3 | 435.8 | 3 | 0 |
| /integrations | 29.9 | 223.1 | 237.7 | 2 | 0 |
| /settings | 47 | 413.4 | 417 | 10 | 0 |
| /system-health | 27.2 | 319.5 | 0 | 0 | 0 |

## /dashboard waterfall (first document load)

| path | start | end |
|---|---:|---:|
| /api/runtime-config | 145.2 | 150.6 |
| /api/auth/csrf | 165.5 | 177.9 |
| /api/auth/me | 165.8 | 220.9 |
| /api/dashboard/executive-summary | 233.9 | 358.4 |
| /api/ops/monitoring/runtime-status | 238.2 | 366.9 |
| /api/stream/alerts | 241.5 | 353.5 |
| /api/account/pilot-access | 242.6 | 405.6 |
| /api/account/plan | 243.6 | 443.2 |
| /api/dashboard/executive-summary | 812.3 | 871.1 |

