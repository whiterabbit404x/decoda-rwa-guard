import { expect, test } from '@playwright/test';

import { fallbackThreatDashboard, fetchDashboardPageData } from '../app/dashboard-data';

test('dashboard payloads sanitize internal monitoring provenance terms', async () => {
  const originalFetch = global.fetch;

  global.fetch = (async (input: string | URL | Request) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    const pathname = new URL(url).pathname;

    if (pathname === '/dashboard') {
      return new Response(JSON.stringify({ mode: 'local', database_url: null, redis_enabled: false, cards: [], services: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const payloadByPath = {
      '/risk/dashboard': { source: 'fallback', degraded: true, message: 'fallback scenario', risk_engine: { url: 'x', timeout_seconds: 1, live_items: 0, fallback_items: 0 }, generated_at: new Date(0).toISOString(), summary: { total_transactions: 0, allow_count: 0, review_count: 0, block_count: 0, avg_risk_score: 0, high_alert_count: 0 }, transaction_queue: [], risk_alerts: [], contract_scan_results: [], decisions_log: [] },
      '/threat/dashboard': fallbackThreatDashboard,
      '/compliance/dashboard': { ...({ source: 'fallback', degraded: true, generated_at: new Date(0).toISOString(), summary: { allowlisted_wallet_count: 0, blocklisted_wallet_count: 0, frozen_wallet_count: 0, review_required_wallet_count: 0, paused_asset_count: 0, latest_transfer_decision: 'review', latest_residency_decision: 'review', triggered_rule_count: 0 }, cards: [], transfer_screening: { decision: 'review', risk_level: 'low', reasons: [], triggered_rules: [], recommended_action: '', wrapper_status: '', explainability_summary: 'demo_scenario', policy_snapshot: {} }, residency_screening: { residency_decision: 'review', policy_violations: [], routing_recommendation: '', governance_status: '', explainability_summary: 'synthetic', allowed_region_outcome: '' }, policy_state: { allowlisted_wallets: [], blocklisted_wallets: [], frozen_wallets: [], review_required_wallets: [], paused_assets: [], approved_cloud_regions: [], friendly_regions: [], restricted_regions: [], action_count: 0, latest_action_id: null }, latest_governance_actions: [], asset_transfer_status: [], sample_scenarios: {}, message: 'fallback scenario' }) },
      '/resilience/dashboard': { source: 'fallback', degraded: true, generated_at: new Date(0).toISOString(), summary: { reconciliation_status: 'warning', severity_score: 0, mismatch_amount: 0, stale_ledger_count: 0, backstop_decision: 'alert', incident_count: 0 }, cards: [], reconciliation_result: { asset_id: 'x', reconciliation_status: 'warning', expected_total_supply: 0, observed_total_supply: 0, normalized_effective_supply: 0, mismatch_amount: 0, mismatch_percent: 0, severity_score: 0, duplicate_or_double_count_risk: false, stale_ledger_count: 0, settlement_lag_ledgers: [], mismatch_summary: [], recommendations: [], explainability_summary: 'sample scenario', per_ledger_balances: [], ledger_assessments: [] }, backstop_result: { asset_id: 'x', backstop_decision: 'alert', triggered_safeguards: [], recommended_actions: [], operational_status: '', trading_status: '', bridge_status: '', settlement_status: '', explainability_summary: 'demo' }, latest_incidents: [], sample_scenarios: {}, message: 'demo_scenario' },
    } as Record<string, unknown>;

    return new Response(JSON.stringify(payloadByPath[pathname]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof global.fetch;

  try {
    const data = await fetchDashboardPageData('https://railway.example');
    const customerCopy = [
      data.threatDashboard.message,
      ...data.threatDashboard.cards.map((card) => card.detail),
      ...data.threatDashboard.active_alerts.map((alert) => `${alert.title} ${alert.explanation}`),
      data.complianceDashboard.message,
      ...data.complianceDashboard.cards.map((card) => card.detail),
      data.resilienceDashboard.message,
      ...data.resilienceDashboard.cards.map((card) => card.detail),
    ].join(' ').toLowerCase();

    ['demo', 'synthetic', 'scenario', 'hybrid'].forEach((term) => {
      expect(customerCopy.includes(term)).toBe(false);
    });
  } finally {
    global.fetch = originalFetch;
  }
});
