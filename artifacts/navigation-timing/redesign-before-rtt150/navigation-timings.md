# Navigation timings — capture

Captured 2026-10-23T03:01:32.235Z against http://127.0.0.1:3100; 3 load(s) per route, medians shown; added round-trip latency 150ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 11.5 | 240 | 480.2 | 616.8 | 641.7 | 6 | 0 |
| /assets | 15.4 | 260 | 504.5 | 669.1 | 711.1 | 6 | 0 |
| /monitoring-sources | 13.6 | 260 | 531.2 | 667.1 | 740 | 6 | 0 |
| /threat | 17 | 244 | 484 | 641.1 | 651.7 | 5 | 0 |
| /alerts | 12.4 | 252 | 497.2 | 645.8 | 657.4 | 5 | 0 |
| /incidents | 12.9 | 264 | 515.4 | 893.2 | 894.9 | 10 | 0 |
| /response-actions | 13.8 | 252 | 553.9 | 769 | 777.4 | 8 | 0 |
| /evidence | 14 | 252 | 520.8 | 709.8 | 693.6 | 7 | 0 |
| /integrations | 10.8 | 256 | 509.6 | 716.5 | 703.5 | 6 | 0 |
| /settings | 11.7 | 248 | 539.4 | 897.8 | 995.6 | 15 | 0 |
| /system-health | 16.2 | 240 | 527.7 | 443.1 | 685.8 | 4 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | feedback | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|
| /assets | 188 | 193.2 | 358.9 | 362.3 | 2 | 0 |
| /monitoring-sources | 201.9 | 206.1 | 363.3 | 369.2 | 2 | 0 |
| /threat | 189.5 | 194.8 | 347.8 | 354.7 | 1 | 0 |
| /alerts | 183.7 | 185.7 | 331.1 | 345.8 | 1 | 0 |
| /incidents | 184 | 187.4 | 515.6 | 524 | 6 | 0 |
| /response-actions | 179.7 | 177.5 | 617.9 | 628.5 | 4 | 0 |
| /evidence | 176.7 | 171.3 | 611.4 | 623.8 | 3 | 0 |
| /integrations | 178.5 | 178.7 | 337.2 | 335.4 | 2 | 0 |
| /settings | 192.1 | 194.5 | 541.2 | 552.4 | 11 | 0 |
| /system-health | 215.5 | 173.4 | 0 | 0 | 0 | 0 |

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
| /api/auth/csrf | 296.2 | 450.7 |
| /api/auth/me | 297.5 | 451.4 |
| /api/dashboard/executive-summary | 473.5 | 629.6 |
| /api/ops/monitoring/runtime-status | 476.2 | 632.8 |
| /api/stream/alerts | 477.8 | 639.5 |
| /api/account/plan | 479 | 641.7 |

