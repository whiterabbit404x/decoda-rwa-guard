# Navigation timings — capture

Captured 2026-10-23T03:10:18.866Z against http://127.0.0.1:3100; 3 load(s) per route, medians shown; added round-trip latency 0ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 14.5 | 120 | 252.4 | 356 | 380.8 | 6 | 0 |
| /assets | 12.2 | 108 | 291 | 400 | 426.5 | 6 | 0 |
| /monitoring-sources | 14.8 | 104 | 289.2 | 426.4 | 470 | 6 | 0 |
| /threat | 11.8 | 116 | 274.2 | 353.5 | 452.7 | 5 | 0 |
| /alerts | 14.8 | 120 | 281.5 | 351.5 | 392.6 | 5 | 0 |
| /incidents | 11.4 | 100 | 245.8 | 551.2 | 552.2 | 10 | 0 |
| /response-actions | 13.6 | 112 | 268.8 | 504.4 | 501.3 | 8 | 0 |
| /evidence | 10.9 | 112 | 282.7 | 436.3 | 451 | 7 | 0 |
| /integrations | 16 | 108 | 303.6 | 474.7 | 483.5 | 6 | 0 |
| /settings | 11.3 | 120 | 346.7 | 649.6 | 723.7 | 15 | 0 |
| /system-health | 15 | 136 | 330.7 | 257.1 | 388.6 | 4 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | feedback | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|
| /assets | 48.6 | 51.4 | 168.4 | 160.4 | 2 | 0 |
| /monitoring-sources | 54.4 | 58.8 | 161.5 | 168.7 | 2 | 0 |
| /threat | 41.1 | 53.7 | 114.3 | 121.7 | 1 | 0 |
| /alerts | 40.9 | 45.2 | 86.1 | 95.8 | 1 | 0 |
| /incidents | 39.2 | 43.6 | 297.9 | 292.3 | 6 | 0 |
| /response-actions | 29.4 | 28.4 | 461.7 | 462.6 | 4 | 0 |
| /evidence | 35.2 | 28.7 | 415.1 | 424.3 | 3 | 0 |
| /integrations | 32.4 | 37.7 | 165 | 176.3 | 2 | 0 |
| /settings | 58 | 61.6 | 405.7 | 419.3 | 11 | 0 |
| /system-health | 29 | 27 | 314 | 0 | 0 | 0 |

## /dashboard waterfall (first document load)

| path | start | end |
|---|---:|---:|
| /api/auth/csrf | 168.2 | 203.9 |
| /api/auth/me | 168.7 | 226.7 |
| /api/dashboard/executive-summary | 245.6 | 343.5 |
| /api/ops/monitoring/runtime-status | 250.2 | 267.5 |
| /api/stream/alerts | 261 | 335 |
| /api/account/plan | 264.3 | 380.8 |

