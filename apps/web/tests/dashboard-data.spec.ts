import { expect, test } from '@playwright/test';

import { normalizeApiBaseUrl, resolveApiConfig } from '../app/api-config';
import {
  buildDashboardViewModel,
  fallbackComplianceDashboard,
  fallbackResilienceDashboard,
  fallbackRiskDashboard,
  fallbackThreatDashboard,
  fetchDashboardPageData,
  formatSourceLabel,
  shouldRenderThreatAlertSourceChip,
} from '../app/dashboard-data';
import { GET as getDashboardPageData } from '../app/api/dashboard-page-data/route';

const dashboardPayload = {
  mode: 'production',
  database_url: 'postgres://railway',
  redis_enabled: true,
  cards: [
    {
      title: 'API Gateway',
      status: 'Healthy',
      detail: 'Gateway reachable.',
      service: 'api',
    },
  ],
  services: [
    {
      service_name: 'api',
      port: 8000,
      status: 'ok',
      detail: 'Gateway healthy.',
      updated_at: '2026-03-20T00:00:00Z',
    },
  ],
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function liveRiskDashboard() {
  const payload = clone(fallbackRiskDashboard);
  payload.source = 'live';
  payload.degraded = false;
  payload.message = 'Live risk dashboard connected.';
  payload.risk_engine.url = 'https://railway.example';
  payload.risk_engine.live_items = payload.summary.total_transactions;
  payload.risk_engine.fallback_items = 0;
  payload.transaction_queue.forEach((item) => {
    item.source = 'live';
  });
  payload.contract_scan_results.forEach((item) => {
    item.source = 'live';
  });
  payload.decisions_log.forEach((item) => {
    item.source = 'live';
  });
  return payload;
}

function liveThreatDashboard() {
  const payload = clone(fallbackThreatDashboard);
  payload.source = 'live';
  payload.degraded = false;
  payload.message = 'Threat monitoring scores use deterministic weighted rules with auditable evidence.';
  payload.cards = [
    { label: 'Threat score', value: '82', detail: 'Contract scan composite score from deterministic rules.', tone: 'critical' },
    { label: 'Active alerts', value: '4', detail: 'Critical and high-confidence exploit or anomaly detections.', tone: 'high' },
    { label: 'Blocked / reviewed', value: '3/2', detail: 'Action decisions produced by the explainable scoring layer.', tone: 'medium' },
    { label: 'Market anomaly avg', value: '70.0', detail: 'Average anomaly score across monitored treasury-token activity.', tone: 'high' },
  ];
  payload.active_alerts.forEach((item) => {
    item.source = 'live';
  });
  payload.recent_detections.forEach((item) => {
    item.source = 'live';
  });
  return payload;
}

function liveComplianceDashboard() {
  const payload = clone(fallbackComplianceDashboard);
  payload.source = 'live';
  payload.degraded = false;
  payload.message = 'Live compliance dashboard connected.';
  return payload;
}

function liveResilienceDashboard() {
  const payload = clone(fallbackResilienceDashboard);
  payload.source = 'live';
  payload.degraded = false;
  payload.message = 'Live resilience dashboard connected.';
  payload.latest_incidents.forEach((item) => {
    item.source = 'live';
    item.degraded = false;
  });
  return payload;
}

test.describe('dashboard production API flow', () => {
  test('normalizes API base URLs by trimming whitespace and trailing slashes', async () => {
    expect(normalizeApiBaseUrl(' https://api.decoda.example/// ')).toBe('https://api.decoda.example');
    expect(normalizeApiBaseUrl('   ')).toBeNull();
  });

  test('requires API_URL or NEXT_PUBLIC_API_URL in production instead of falling back to localhost', async () => {
    const config = resolveApiConfig({
      env: {
        NODE_ENV: 'production',
      } as NodeJS.ProcessEnv,
    });

    expect(config.apiUrl).toBeNull();
    expect(config.source).toBe('missing');
    expect(config.diagnostic).toContain('API_URL or NEXT_PUBLIC_API_URL');
  });

  test('keeps the experience live when /dashboard returns an empty registry but all feature feeds are live', async () => {
    const originalFetch = global.fetch;

    global.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const pathname = new URL(url).pathname;

      const payloadByPath = {
        '/dashboard': {
          mode: 'production',
          database_url: 'postgres://railway',
          redis_enabled: true,
          cards: [],
          services: [],
        },
        '/risk/dashboard': liveRiskDashboard(),
        '/threat/dashboard': liveThreatDashboard(),
        '/compliance/dashboard': liveComplianceDashboard(),
        '/resilience/dashboard': liveResilienceDashboard(),
      } satisfies Record<string, unknown>;

      return new Response(JSON.stringify(payloadByPath[pathname]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof global.fetch;

    try {
      const data = await fetchDashboardPageData('https://railway.example');
      const viewModel = buildDashboardViewModel(data);
      const response = await getDashboardPageData(new Request('https://web.example/api/dashboard-page-data?apiUrl=https%3A%2F%2Frailway.example'));
      const payload = (await response.json()) as {
        meta: {
          live: boolean;
          experienceState: string;
          diagnostics: {
            experienceState: string;
            fallbackTriggered: boolean;
          };
        };
      };

      expect(data.dashboard).not.toBeNull();
      expect(data.dashboard?.cards).toEqual([]);
      expect(data.dashboard?.services).toEqual([]);
      expect(data.diagnostics.endpoints.dashboard.payloadState).toBe('live');
      expect(data.diagnostics.experienceState).toBe('live');
      expect(viewModel.backendState).toBe('online');
      expect(viewModel.summaryCards.some((card) => card.meta.includes('Coverage currently limited'))).toBe(false);
      expect(payload.meta.live).toBe(true);
      expect(payload.meta.experienceState).toBe('live');
      expect(payload.meta.diagnostics.fallbackTriggered).toBe(false);
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('reports live Railway metadata when all dashboard endpoints succeed', async () => {
    const originalFetch = global.fetch;
    const requestedUrls: string[] = [];

    global.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      requestedUrls.push(url);

      const pathname = new URL(url).pathname;
      const payloadByPath = {
        '/dashboard': dashboardPayload,
        '/risk/dashboard': liveRiskDashboard(),
        '/threat/dashboard': liveThreatDashboard(),
        '/compliance/dashboard': liveComplianceDashboard(),
        '/resilience/dashboard': liveResilienceDashboard(),
      } satisfies Record<string, unknown>;

      return new Response(JSON.stringify(payloadByPath[pathname]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof global.fetch;

    try {
      const data = await fetchDashboardPageData('https://railway.example///');
      const viewModel = buildDashboardViewModel(data);

      expect(data.apiUrl).toBe('https://railway.example');
      expect(viewModel.backendState).toBe('online');
      expect(data.diagnostics.apiUrl).toBe('https://railway.example');
      expect(data.diagnostics.apiUrlSource).toBe('request');
      expect(data.diagnostics.fallbackTriggered).toBe(false);
      expect(data.diagnostics.failedEndpoints).toEqual([]);
      expect(data.diagnostics.endpoints.riskDashboard.source).toBe('live');
      expect(requestedUrls).toEqual([
        'https://railway.example/ops/dashboard-page-data',
        'https://railway.example/dashboard',
        'https://railway.example/risk/dashboard',
        'https://railway.example/threat/dashboard',
        'https://railway.example/compliance/dashboard',
        'https://railway.example/resilience/dashboard',
      ]);

      const response = await getDashboardPageData(new Request('https://web.example/api/dashboard-page-data?apiUrl=https%3A%2F%2Frailway.example%2F'));
      const payload = (await response.json()) as {
        meta: {
          diagnostics: {
            apiUrl: string | null;
            fallbackTriggered: boolean;
            failedEndpoints: string[];
            experienceState?: string;
          };
          experienceState?: string;
        };
      };

      expect(payload.meta.diagnostics.apiUrl).toBe('https://railway.example');
      expect(payload.meta.diagnostics.fallbackTriggered).toBe(false);
      expect(payload.meta.diagnostics.failedEndpoints).toEqual([]);
      expect(payload.meta.experienceState ?? payload.meta.diagnostics.experienceState).toBe('live');
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('enters degraded mode only after a live fetch fails and records the failing endpoint', async () => {
    const originalFetch = global.fetch;

    global.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const pathname = new URL(url).pathname;

      if (pathname === '/resilience/dashboard') {
        return new Response(JSON.stringify({ detail: 'upstream timeout' }), { status: 503 });
      }

      const payloadByPath = {
        '/dashboard': dashboardPayload,
        '/risk/dashboard': liveRiskDashboard(),
        '/threat/dashboard': liveThreatDashboard(),
        '/compliance/dashboard': liveComplianceDashboard(),
      } satisfies Record<string, unknown>;

      return new Response(JSON.stringify(payloadByPath[pathname]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof global.fetch;

    try {
      const data = await fetchDashboardPageData('https://railway.example');
      const viewModel = buildDashboardViewModel(data);

      expect(viewModel.backendState).toBe('degraded');
      expect(data.diagnostics.experienceState).toBe('live_degraded');
      expect(data.diagnostics.fallbackTriggered).toBe(true);
      expect(data.diagnostics.failedEndpoints).toEqual(['resilienceDashboard']);
      expect(data.diagnostics.endpoints.resilienceDashboard.usedFallback).toBe(true);
      expect(data.diagnostics.endpoints.resilienceDashboard.payloadState).toBe('unavailable');
      expect(data.diagnostics.endpoints.resilienceDashboard.error).toContain('503');
      expect(data.resilienceDashboard.source).toBe('fallback');
      expect(viewModel.summaryCards.find((card) => card.label === 'Resilience status')?.meta).toContain('Telemetry unavailable');
      expect(viewModel.summaryCards.some((card) => card.meta.includes('sample'))).toBe(false);
      expect(viewModel.backendBanner).toContain('/resilience/dashboard');
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('treats backend fallback payloads as degraded fallback coverage instead of sample coverage', async () => {
    const originalFetch = global.fetch;

    global.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const pathname = new URL(url).pathname;

      const payloadByPath = {
        '/dashboard': dashboardPayload,
        '/risk/dashboard': liveRiskDashboard(),
        '/threat/dashboard': { ...liveThreatDashboard(), source: 'fallback', degraded: true, message: 'Threat fallback payload from gateway.' },
        '/compliance/dashboard': liveComplianceDashboard(),
        '/resilience/dashboard': liveResilienceDashboard(),
      } satisfies Record<string, unknown>;

      return new Response(JSON.stringify(payloadByPath[pathname]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof global.fetch;

    try {
      const data = await fetchDashboardPageData('https://railway.example');
      const viewModel = buildDashboardViewModel(data);
      const response = await getDashboardPageData(new Request('https://web.example/api/dashboard-page-data?apiUrl=https%3A%2F%2Frailway.example'));
      const routePayload = (await response.json()) as {
        meta: {
          threatDiagnostics: {
            presentationState: string;
            degraded: boolean;
            activeAlerts: number;
            endpoint: {
              path: string;
              payloadState: string;
            };
          };
        };
      };

      expect(data.diagnostics.endpoints.threatDashboard.transport).toBe('ok');
      expect(data.diagnostics.endpoints.threatDashboard.payloadState).toBe('limited_coverage');
      expect(data.diagnostics.experienceState).toBe('live_degraded');
      expect(viewModel.backendState).toBe('degraded');
      expect(viewModel.summaryCards.find((card) => card.label === 'Threat posture')?.meta).toContain('Coverage currently limited');
      expect(data.threatDashboard.message.toLowerCase()).not.toContain('fallback');
      expect(viewModel.summaryCards.some((card) => card.meta.includes('sample'))).toBe(false);
      expect(formatSourceLabel(data.diagnostics.endpoints.threatDashboard.payloadState)).toBe('Coverage currently limited');
      expect(routePayload.meta.threatDiagnostics).toEqual(expect.objectContaining({
        presentationState: 'limited_coverage',
        degraded: true,
        activeAlerts: data.threatDashboard.active_alerts.length,
        endpoint: expect.objectContaining({
          path: '/threat/dashboard',
          payloadState: 'limited_coverage',
        }),
      }));
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('normalizes Feature 2 copy and labels to live when the payload is live but still contains stale fallback text', async () => {
    const originalFetch = global.fetch;

    global.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const pathname = new URL(url).pathname;

      const staleLiveThreatPayload = {
        ...clone(fallbackThreatDashboard),
        source: 'live',
        degraded: false,
      };

      const payloadByPath = {
        '/dashboard': dashboardPayload,
        '/risk/dashboard': liveRiskDashboard(),
        '/threat/dashboard': staleLiveThreatPayload,
        '/compliance/dashboard': liveComplianceDashboard(),
        '/resilience/dashboard': liveResilienceDashboard(),
      } satisfies Record<string, unknown>;

      return new Response(JSON.stringify(payloadByPath[pathname]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof global.fetch;

    try {
      const data = await fetchDashboardPageData('https://railway.example');
      const viewModel = buildDashboardViewModel(data);
      const response = await getDashboardPageData(new Request('https://web.example/api/dashboard-page-data?apiUrl=https%3A%2F%2Frailway.example'));
      const routePayload = (await response.json()) as {
        meta: {
          threatDiagnostics: {
            presentationState: string;
            degraded: boolean;
            message: string;
            activeAlerts: number;
            endpoint: {
              path: string;
              payloadState: string;
            };
          };
        };
      };

      expect(data.threatDashboard.source).toBe('live');
      expect(data.threatDashboard.degraded).toBe(false);
      expect(data.threatDashboard.message).toBe('Threat monitoring scores use deterministic weighted rules with auditable evidence.');
      expect(data.threatDashboard.cards.some((card) => card.detail.toLowerCase().includes('fallback'))).toBe(false);
      expect(data.threatDashboard.active_alerts.every((alert) => alert.source === 'live')).toBe(true);
      expect(data.threatDashboard.recent_detections.every((detection) => detection.source === 'live')).toBe(true);
      expect(data.diagnostics.endpoints.threatDashboard.payloadState).toBe('live');
      expect(viewModel.backendState).toBe('online');
      expect(viewModel.summaryCards.find((card) => card.label === 'Threat posture')?.meta).toContain('Verified telemetry');
      expect(routePayload.meta.threatDiagnostics).toEqual({
        presentationState: 'live',
        degraded: false,
        message: 'Threat monitoring scores use deterministic weighted rules with auditable evidence.',
        activeAlerts: data.threatDashboard.active_alerts.length,
        endpoint: expect.objectContaining({
          path: '/threat/dashboard',
          payloadState: 'live',
        }),
      });
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('treats live Feature 2 payloads as live rendering mode without fallback alert chips', async () => {
    const originalFetch = global.fetch;

    global.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const pathname = new URL(url).pathname;

      const staleLiveThreatPayload = {
        ...clone(fallbackThreatDashboard),
        source: 'live',
        degraded: false,
      };

      const payloadByPath = {
        '/dashboard': dashboardPayload,
        '/risk/dashboard': liveRiskDashboard(),
        '/threat/dashboard': staleLiveThreatPayload,
        '/compliance/dashboard': liveComplianceDashboard(),
        '/resilience/dashboard': liveResilienceDashboard(),
      } satisfies Record<string, unknown>;

      return new Response(JSON.stringify(payloadByPath[pathname]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof global.fetch;

    try {
      const data = await fetchDashboardPageData('https://railway.example');

      expect(data.threatDashboard.message).not.toContain('Threat-engine unavailable or timed out');
      expect(data.threatDashboard.cards.some((card) => card.detail.includes('Fallback contract threat score'))).toBe(false);
      expect(data.threatDashboard.active_alerts.every((alert) => !shouldRenderThreatAlertSourceChip(data.threatDashboard, alert))).toBe(true);
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('treats true fallback Feature 2 payloads as fallback rendering mode with fallback alert chips', async () => {
    const originalFetch = global.fetch;

    global.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const pathname = new URL(url).pathname;

      const payloadByPath = {
        '/dashboard': dashboardPayload,
        '/risk/dashboard': liveRiskDashboard(),
        '/threat/dashboard': clone(fallbackThreatDashboard),
        '/compliance/dashboard': liveComplianceDashboard(),
        '/resilience/dashboard': liveResilienceDashboard(),
      } satisfies Record<string, unknown>;

      return new Response(JSON.stringify(payloadByPath[pathname]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof global.fetch;

    try {
      const data = await fetchDashboardPageData('https://railway.example');

      expect(data.threatDashboard.source).toBe('fallback');
      expect(data.threatDashboard.degraded).toBe(true);
      expect(data.threatDashboard.cards.some((card) => card.detail.includes('Coverage currently degraded'))).toBe(true);
      expect(data.threatDashboard.message).toContain('Live evidence unavailable');
      expect(data.threatDashboard.active_alerts.some((alert) => shouldRenderThreatAlertSourceChip(data.threatDashboard, alert) && alert.source === 'fallback')).toBe(true);
      expect(data.diagnostics.endpoints.threatDashboard.path).toBe('/threat/dashboard');
      expect(formatSourceLabel(data.diagnostics.endpoints.threatDashboard.payloadState)).toBe('Coverage currently limited');
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('enters degraded telemetry mode when production has no live API configured', async () => {
    const originalNodeEnv = process.env.NODE_ENV;
    const originalApiUrl = process.env.NEXT_PUBLIC_API_URL;

    delete process.env.NEXT_PUBLIC_API_URL;
    process.env.NODE_ENV = 'production';

    try {
      const data = await fetchDashboardPageData();
      const viewModel = buildDashboardViewModel(data);

      expect(data.apiUrl).toBe('');
      expect(data.diagnostics.sampleMode).toBe(true);
      expect(data.diagnostics.experienceState).toBe('limited_coverage');
      expect(data.diagnostics.endpoints.riskDashboard.transport).toBe('skipped');
      expect(data.diagnostics.endpoints.riskDashboard.payloadState).toBe('limited_coverage');
      expect(viewModel.backendState).toBe('degraded');
      expect(viewModel.summaryCards.some((card) => card.meta.includes('Coverage currently limited'))).toBe(true);
      expect(viewModel.backendBanner).not.toContain('sample mode');
    } finally {
      process.env.NODE_ENV = originalNodeEnv;
      if (originalApiUrl === undefined) {
        delete process.env.NEXT_PUBLIC_API_URL;
      } else {
        process.env.NEXT_PUBLIC_API_URL = originalApiUrl;
      }
    }
  });
});
