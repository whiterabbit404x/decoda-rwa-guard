# Navigation timings — capture

Captured 2026-10-23T03:08:01.826Z against http://127.0.0.1:3000; 3 load(s) per route, medians shown; added round-trip latency 150ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 13.9 | 248 | 507.7 | 637.9 | 664.1 | 6 | 0 |
| /assets | 22.6 | 236 | 492.1 | 642.5 | 661.7 | 6 | 0 |
| /monitoring-sources | 13 | 244 | 523.2 | 665.1 | 705.8 | 6 | 0 |
| /threat | 13.8 | 240 | 490.4 | 641.5 | 646.8 | 5 | 0 |
| /alerts | 16.8 | 244 | 488.3 | 640.6 | 654.2 | 5 | 0 |
| /incidents | 13.5 | 260 | 498.5 | 670.5 | 708.9 | 7 | 0 |
| /response-actions | 15.2 | 248 | 518 | 689.7 | 675.9 | 8 | 0 |
| /evidence | 19 | 248 | 509.3 | 679.2 | 666.6 | 7 | 0 |
| /integrations | 14 | 248 | 475.5 | 643.1 | 646.9 | 6 | 0 |
| /settings | 12.4 | 248 | 517.9 | 847.7 | 974.2 | 15 | 0 |
| /system-health | 15.2 | 248 | 519 | 450.1 | 696.1 | 4 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | feedback | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|
| /assets | 13.5 | 32.4 | 185.7 | 193.1 | 2 | 0 |
| /monitoring-sources | 25.6 | 33.8 | 188.1 | 195.4 | 2 | 0 |
| /threat | 10.7 | 33.1 | 181 | 183.2 | 1 | 0 |
| /alerts | 17.3 | 19.8 | 168.6 | 179.7 | 1 | 0 |
| /incidents | 15.7 | 19.1 | 173.2 | 180.3 | 3 | 0 |
| /response-actions | 22.2 | 30 | 194.3 | 198.5 | 4 | 0 |
| /evidence | 14.4 | 36.2 | 196.5 | 199.3 | 3 | 0 |
| /integrations | 16.5 | 23.5 | 169.7 | 180.3 | 2 | 0 |
| /settings | 11.1 | 38.1 | 370 | 375.7 | 11 | 0 |
| /system-health | 10.6 | 174 | 179.4 | 0 | 0 | 0 |

## Steady state (120s on /dashboard)

10 requests (5/min).

| path | requests |
|---|---:|
| /api/stream/alerts | 5 |
| /api/dashboard/executive-summary | 4 |
| /api/ops/monitoring/runtime-status | 1 |

## Steady state (120s on /incidents)

1 requests (0.5/min).

| path | requests |
|---|---:|
| /api/ops/monitoring/runtime-status | 1 |

## /dashboard waterfall (first document load)

| path | start | end |
|---|---:|---:|
| /api/auth/csrf | 314 | 475 |
| /api/auth/me | 314.6 | 475.3 |
| /api/dashboard/executive-summary | 499.4 | 656 |
| /api/ops/monitoring/runtime-status | 502.3 | 656.8 |
| /api/stream/alerts | 507.4 | 661 |
| /api/account/plan | 510.5 | 664.1 |

