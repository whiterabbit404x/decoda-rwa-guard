# Navigation timings — after-rtt150

Captured 2026-10-22T14:46:52.905Z against http://127.0.0.1:3000; 3 load(s) per route, medians shown; added round-trip latency 150ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 15.2 | 232 | 500.1 | 645.9 | 677.4 | 6 | 0 |
| /assets | 17.8 | 236 | 474.7 | 618.4 | 635.2 | 6 | 0 |
| /monitoring-sources | 12.6 | 232 | 521.8 | 666.8 | 713.3 | 6 | 0 |
| /threat | 14.9 | 236 | 479.1 | 617.1 | 642.8 | 5 | 0 |
| /alerts | 14.1 | 260 | 490.7 | 638.3 | 645 | 5 | 0 |
| /incidents | 17.2 | 256 | 504.8 | 642.4 | 694.9 | 7 | 0 |
| /response-actions | 13.5 | 256 | 516.9 | 791.3 | 831.5 | 8 | 0 |
| /evidence | 15.7 | 240 | 503.8 | 728.2 | 698.2 | 7 | 0 |
| /integrations | 14.6 | 244 | 480.5 | 691.2 | 690.7 | 6 | 0 |
| /settings | 15 | 248 | 522.6 | 967.6 | 982.7 | 15 | 0 |
| /system-health | 20.8 | 236 | 546.7 | 466.3 | 706.8 | 4 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|
| /assets | 186.5 | 335.1 | 350 | 2 | 0 |
| /monitoring-sources | 199.5 | 352 | 357.2 | 2 | 0 |
| /threat | 183.6 | 335.4 | 345.8 | 1 | 0 |
| /alerts | 180 | 325.6 | 334.9 | 1 | 0 |
| /incidents | 179.8 | 319.1 | 332.9 | 3 | 0 |
| /response-actions | 174.6 | 610.4 | 622.4 | 4 | 0 |
| /evidence | 175.8 | 601.9 | 619.9 | 3 | 0 |
| /integrations | 174.3 | 317.8 | 329.7 | 2 | 0 |
| /settings | 186.1 | 595.9 | 598.3 | 11 | 0 |
| /system-health | 167.4 | 0 | 0 | 0 | 0 |

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
| /api/auth/csrf | 320.7 | 482 |
| /api/auth/me | 321 | 481.6 |
| /api/dashboard/executive-summary | 508.1 | 832.7 |
| /api/ops/monitoring/runtime-status | 510.6 | 797.7 |
| /api/stream/alerts | 514.7 | 670.2 |
| /api/account/plan | 516.6 | 673.7 |

