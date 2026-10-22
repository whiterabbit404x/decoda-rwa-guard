# Navigation timings — before-rtt150

Captured 2026-10-22T14:24:05.275Z against http://127.0.0.1:3000; 3 load(s) per route, medians shown; added round-trip latency 150ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 15.7 | 228 | 631.3 | 763.9 | 1368.9 | 9 | 1 |
| /assets | 16.7 | 236 | 634.1 | 832.3 | 861.2 | 8 | 0 |
| /monitoring-sources | 13.4 | 240 | 650.6 | 942 | 910.1 | 8 | 0 |
| /threat | 12.8 | 232 | 634.2 | 790.3 | 818.7 | 7 | 0 |
| /alerts | 16.7 | 232 | 658.6 | 793.5 | 813.9 | 7 | 0 |
| /incidents | 14.7 | 228 | 626.1 | 938.6 | 947.4 | 9 | 0 |
| /response-actions | 14.6 | 228 | 633.3 | 588.1 | 961.9 | 10 | 0 |
| /evidence | 16.3 | 228 | 645.3 | 976.4 | 968.5 | 9 | 0 |
| /integrations | 14.3 | 232 | 630.2 | 916.1 | 930.7 | 8 | 0 |
| /settings | 16.2 | 232 | 659.6 | 1038.2 | 1124.3 | 16 | 0 |
| /system-health | 15.9 | 232 | 692.4 | 615.5 | 855.1 | 6 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|
| /assets | 194.7 | 360 | 364.3 | 2 | 0 |
| /monitoring-sources | 211.8 | 380.6 | 390.5 | 2 | 0 |
| /threat | 183.5 | 335.8 | 344.5 | 1 | 0 |
| /alerts | 180.1 | 321.8 | 339.8 | 1 | 0 |
| /incidents | 178.3 | 328 | 340.6 | 3 | 0 |
| /response-actions | 172.5 | 0 | 626.5 | 4 | 0 |
| /evidence | 170.7 | 611 | 617.8 | 3 | 0 |
| /integrations | 176 | 373.4 | 375.6 | 2 | 0 |
| /settings | 193.3 | 546 | 551.3 | 10 | 0 |
| /system-health | 171.9 | 0 | 0 | 0 | 0 |

## Steady state (120s on /dashboard)

43 requests (21.5/min).

| path | requests |
|---|---:|
| /api/stream/alerts | 38 |
| /api/dashboard/executive-summary | 4 |
| /api/ops/monitoring/runtime-status | 1 |

## Steady state (120s on /incidents)

4 requests (2/min).

| path | requests |
|---|---:|
| /api/ops/monitoring/runtime-status | 1 |
| /api/incidents | 1 |
| /api/alerts | 1 |
| /api/incidents/summary | 1 |

## /dashboard waterfall (first document load)

| path | start | end |
|---|---:|---:|
| /api/runtime-config | 284.5 | 439.1 |
| /api/auth/csrf | 448.9 | 603 |
| /api/auth/me | 449.2 | 603.4 |
| /api/dashboard/executive-summary | 634.8 | 967.2 |
| /api/ops/monitoring/runtime-status | 637 | 913.9 |
| /api/stream/alerts | 641.5 | 797.2 |
| /api/account/pilot-access | 642.6 | 797.7 |
| /api/account/plan | 643.5 | 797.4 |
| /api/dashboard/executive-summary | 1320.1 | 1473.9 |

