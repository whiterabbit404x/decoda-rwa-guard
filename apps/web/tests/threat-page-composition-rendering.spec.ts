import { expect, test } from '@playwright/test';

import TechnicalRuntimeDetails from '../app/threat/technical-runtime-details';

type NodeLike = { type?: unknown; props?: Record<string, unknown> };

function getChildren(node: unknown): unknown[] {
  if (!node || typeof node !== 'object') return [];
  const props = (node as NodeLike).props;
  if (!props) return [];
  const children = props.children;
  if (Array.isArray(children)) return children;
  return children === undefined ? [] : [children];
}

function findNode(root: unknown, predicate: (node: NodeLike) => boolean): NodeLike | null {
  const stack: unknown[] = [root];
  while (stack.length > 0) {
    const node = stack.shift();
    if (node && typeof node === 'object') {
      const typed = node as NodeLike;
      if (predicate(typed)) return typed;
      stack.push(...getChildren(node));
    }
  }
  return null;
}

test.describe('threat composition rendering behavior', () => {
  test('technical runtime details are collapsed by default', () => {
    const tree = TechnicalRuntimeDetails({
      summaryLine: 'Diagnostics available',
      contradictionFlags: ['offline_with_current_telemetry'],
      guardFlags: ['live_monitoring_without_reporting_systems'],
      dbFailureClassification: 'quota_exceeded',
    });

    const detailsNode = findNode(tree, (node) => node.type === 'details');
    const summaryNode = findNode(tree, (node) => node.type === 'summary');

    expect(detailsNode).not.toBeNull();
    expect(detailsNode?.props?.open).toBeUndefined();
    expect(summaryNode?.props?.children).toBe('View technical details');
  });

  test('diagnostic internals stay in technical details and out of customer-facing summary copy', () => {
    const technical = TechnicalRuntimeDetails({
      summaryLine: 'Diagnostics available',
      contradictionFlags: ['offline_with_current_telemetry'],
      guardFlags: ['live_monitoring_without_reporting_systems'],
      dbFailureClassification: 'quota_exceeded',
      customerContinuitySummary: 'No live signal received yet',
    });

    const listNode = findNode(technical, (node) => node.type === 'ul');
    const customerSummary = findNode(technical, (node) => node.type === 'p' && String(node.props?.children).includes('customer continuity summary:'));

    const listText = JSON.stringify(listNode);
    const customerText = JSON.stringify(customerSummary);

    expect(listText).toContain('contradiction_flags');
    expect(listText).toContain('guard_flags');
    expect(listText).toContain('db_failure_classification');

    expect(customerText).toContain('No live signal received yet');
    expect(customerText).not.toContain('contradiction_flags');
    expect(customerText).not.toContain('guard_flags');
    expect(customerText).not.toContain('db_failure_classification');
  });
});
