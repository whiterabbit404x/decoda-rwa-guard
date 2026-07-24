/**
 * Screen 3 — risk badge / monitoring-health / formatting helpers (pure logic).
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  formatPercent,
  formatUsd,
  monitoringHealthLabel,
  monitoringHealthVariant,
  relativeTime,
  reserveStatusLabel,
  riskLevelForScore,
  riskLevelLabel,
  riskLevelVariant,
  rwaTypeLabel,
  RISK_SCORE_TOOLTIP,
} from '../app/asset-risk-presentation';

test('risk level thresholds match the canonical 0-100 scale (higher = riskier)', () => {
  expect(riskLevelForScore(0)).toBe('low');
  expect(riskLevelForScore(29)).toBe('low');
  expect(riskLevelForScore(30)).toBe('medium');
  expect(riskLevelForScore(59)).toBe('medium');
  expect(riskLevelForScore(60)).toBe('high');
  expect(riskLevelForScore(79)).toBe('high');
  expect(riskLevelForScore(80)).toBe('critical');
  expect(riskLevelForScore(100)).toBe('critical');
  expect(riskLevelForScore(null)).toBe('unassessed');
  expect(riskLevelForScore(undefined)).toBe('unassessed');
});

test('risk badge variants: low green, medium warning, high/critical danger', () => {
  expect(riskLevelVariant('low')).toBe('success');
  expect(riskLevelVariant('medium')).toBe('warning');
  expect(riskLevelVariant('high')).toBe('danger');
  expect(riskLevelVariant('critical')).toBe('danger');
  expect(riskLevelLabel('unassessed')).toBe('Not assessed');
});

test('risk score tooltip explains higher = greater risk', () => {
  expect(RISK_SCORE_TOOLTIP.toLowerCase()).toContain('higher');
  expect(RISK_SCORE_TOOLTIP.toLowerCase()).toContain('risk');
});

test('monitoring health never turns unknown/missing into healthy', () => {
  expect(monitoringHealthLabel('healthy')).toBe('Healthy');
  expect(monitoringHealthVariant('healthy')).toBe('success');
  expect(monitoringHealthVariant('critical')).toBe('danger');
  expect(monitoringHealthVariant('not_configured')).toBe('neutral');
  expect(monitoringHealthLabel('')).toBe('Unknown');
  expect(monitoringHealthVariant('')).toBe('neutral');
});

test('reserve status labels are truthful (insufficient evidence surfaced)', () => {
  expect(reserveStatusLabel('insufficient_evidence')).toBe('Insufficient evidence');
  expect(reserveStatusLabel('over_collateralized')).toBe('Over-collateralized');
});

test('currency + percent formatting', () => {
  expect(formatUsd(991320000)).toBe('$991.32M');
  expect(formatUsd(3420000000)).toBe('$3.42B');
  expect(formatUsd(null)).toBe('--');
  expect(formatUsd('')).toBe('--');
  expect(formatPercent(128, 0)).toBe('128%');
  expect(formatPercent(null)).toBe('--');
});

test('rwa type labels map canonical keys and fall back gracefully', () => {
  expect(rwaTypeLabel('tokenized_treasury')).toBe('Tokenized Treasury');
  expect(rwaTypeLabel('real_estate')).toBe('Real Estate');
  expect(rwaTypeLabel('', 'contract')).toBe('Contract');
  expect(rwaTypeLabel(null)).toBe('Unclassified');
});

test('relative time is truthful about missing timestamps', () => {
  expect(relativeTime(null)).toBe('never');
  expect(relativeTime('not-a-date')).toBe('never');
  const now = Date.parse('2026-07-24T12:00:00Z');
  expect(relativeTime('2026-07-24T11:59:30Z', now)).toBe('30s ago');
  expect(relativeTime('2026-07-24T11:30:00Z', now)).toBe('30m ago');
  expect(relativeTime('2026-07-24T09:00:00Z', now)).toBe('3h ago');
});

// AI Asset Risk Assessor panel source contract.
const panelSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'asset-risk-assessor-panel.tsx'), 'utf-8');

test('AI panel is the canonical assessor surface (not a chatbot) and consumes the summary API', () => {
  expect(panelSrc).toContain('AI Asset Risk Assessor');
  expect(panelSrc).toContain('Continuous reserve and exposure analysis');
  expect(panelSrc).toContain('Reserve Coverage');
  expect(panelSrc).toContain('Anomaly Warnings');
  expect(panelSrc).toContain('Monitoring Gaps');
  expect(panelSrc).toContain('View full risk report');
  expect(panelSrc).toContain('/api/assets/risk-summary');
  // No free-text chat surface.
  expect(panelSrc).not.toContain('chatbot');
  expect(panelSrc).not.toContain('textarea');
});
