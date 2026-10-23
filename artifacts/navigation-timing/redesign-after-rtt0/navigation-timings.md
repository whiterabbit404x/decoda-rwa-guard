# Navigation timings — capture

Captured 2026-10-23T03:12:32.625Z against http://127.0.0.1:3000; 3 load(s) per route, medians shown; added round-trip latency 0ms; sidebar clicks follow a 100ms hover.

## Document loads (fresh page, warm static cache)

| route | ttfb | fcp | gate | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|---:|
| /dashboard | 14.4 | 120 | 255.6 | 332.3 | 403.1 | 6 | 0 |
| /assets | 15.7 | 132 | 277.5 | 409.3 | 421 | 6 | 0 |
| /monitoring-sources | 12.6 | 132 | 334 | 476.8 | 507.9 | 6 | 0 |
| /threat | 15.3 | 116 | 273.8 | 359.4 | 406.2 | 5 | 0 |
| /alerts | 12.6 | 116 | 264.1 | 312.3 | 347.4 | 5 | 0 |
| /incidents | 14.7 | 120 | 281.1 | 405.3 | 442 | 7 | 0 |
| /response-actions | 12.6 | 124 | 284.6 | 476.6 | 537 | 8 | 0 |
| /evidence | 15 | 108 | 303 | 479.4 | 500.7 | 7 | 0 |
| /integrations | 11.4 | 112 | 240.8 | 403.6 | 414.3 | 6 | 0 |
| /settings | 11.6 | 112 | 291.2 | 665.6 | 660.9 | 15 | 0 |
| /system-health | 15.2 | 132 | 341 | 282.5 | 410.2 | 4 | 0 |

## Sidebar navigation (client-side, from the previous screen)

| route | feedback | url commit | ready | settled | api | dup |
|---|---:|---:|---:|---:|---:|---:|
| /assets | 18 | 48.7 | 111.2 | 124.1 | 2 | 0 |
| /monitoring-sources | 29.7 | 34.1 | 137.1 | 140.3 | 2 | 0 |
| /threat | 12.9 | 35.9 | 95.4 | 93.3 | 1 | 0 |
| /alerts | 9 | 27.6 | 82.2 | 87.7 | 1 | 0 |
| /incidents | 15.1 | 19.5 | 114.2 | 126.9 | 3 | 0 |
| /response-actions | 14.1 | 36 | 168.5 | 184.4 | 4 | 0 |
| /evidence | 24.9 | 32.3 | 135.9 | 153.3 | 3 | 0 |
| /integrations | 13.9 | 18.9 | 146.5 | 155.5 | 2 | 0 |
| /settings | 9.8 | 35.6 | 370.5 | 373.4 | 11 | 0 |
| /system-health | 9.9 | 31.6 | 320.9 | 0 | 0 | 0 |

## /dashboard waterfall (first document load)

| path | start | end |
|---|---:|---:|
| /api/auth/csrf | 164.4 | 196.3 |
| /api/auth/me | 164.7 | 224.2 |
| /api/dashboard/executive-summary | 249.6 | 379.1 |
| /api/ops/monitoring/runtime-status | 253.6 | 378.7 |
| /api/stream/alerts | 255.1 | 334.8 |
| /api/account/plan | 256.6 | 403.1 |

