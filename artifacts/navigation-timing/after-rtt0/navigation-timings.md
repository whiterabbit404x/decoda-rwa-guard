# Navigation timings — after-rtt0

Captured 2026-10-22T14:49:07.158Z against http://127.0.0.1:3000; 3 load(s) per route, medians shown; added round-trip latency 0ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 18.7 | 104 | 219.4 | 309.8 | 350.8 | 6 | 0 |
| /assets | 12 | 84 | 227.6 | 355.2 | 404.2 | 6 | 0 |
| /monitoring-sources | 12 | 96 | 259.7 | 376.5 | 421.3 | 6 | 0 |
| /threat | 11.8 | 96 | 226.8 | 272.1 | 323.8 | 5 | 0 |
| /alerts | 16.3 | 132 | 281.8 | 326.2 | 397.2 | 5 | 0 |
| /incidents | 12.9 | 96 | 226.9 | 319.7 | 387.3 | 7 | 0 |
| /response-actions | 14.1 | 96 | 258.2 | 428.8 | 442.4 | 8 | 0 |
| /evidence | 11.1 | 92 | 254.3 | 444.6 | 452.7 | 7 | 0 |
| /integrations | 15.5 | 108 | 275.9 | 425.4 | 427.5 | 6 | 0 |
| /settings | 15.1 | 96 | 272 | 626.4 | 647.8 | 15 | 0 |
| /system-health | 14.7 | 104 | 309.5 | 227.9 | 381.2 | 4 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|
| /assets | 40.1 | 132.6 | 139.3 | 2 | 0 |
| /monitoring-sources | 51 | 157.3 | 160.6 | 2 | 0 |
| /threat | 35 | 90.9 | 110.5 | 1 | 0 |
| /alerts | 28.1 | 66.7 | 78.9 | 1 | 0 |
| /incidents | 28 | 133.6 | 157.7 | 3 | 0 |
| /response-actions | 23.6 | 443.5 | 440.8 | 4 | 0 |
| /evidence | 25 | 416.2 | 425.3 | 3 | 0 |
| /integrations | 23.7 | 140.2 | 153.7 | 2 | 0 |
| /settings | 43.8 | 348.5 | 358.7 | 11 | 0 |
| /system-health | 25.3 | 306.9 | 0 | 0 | 0 |

## /dashboard waterfall (first document load)

| path | start | end |
|---|---:|---:|
| /api/auth/csrf | 139.6 | 159.5 |
| /api/auth/me | 139.8 | 194.6 |
| /api/dashboard/executive-summary | 208.5 | 310.1 |
| /api/ops/monitoring/runtime-status | 209.8 | 302.9 |
| /api/stream/alerts | 212 | 302.2 |
| /api/account/plan | 212.7 | 350.8 |

