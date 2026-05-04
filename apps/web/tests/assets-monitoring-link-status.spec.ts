import { test, expect } from '@playwright/test';

import { monitoringLinkStatusLabel } from '../app/assets-manager';

test.describe('asset monitoring link status label', () => {
  test('maps backend monitoring_link_status values to expected chip copy', () => {
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'attached' })).toBe('Monitoring attached');
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'system_missing' })).toBe('Monitoring not configured');
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'target_missing' })).toBe('Target missing');
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'not_configured' })).toBe('No targets yet');
  });

  test('never shows attached when monitored systems are missing at runtime', () => {
    const label = monitoringLinkStatusLabel({
      monitoring_link_status: 'system_missing',
      has_linked_monitored_system: false,
      monitoring_target_count: 1,
    });
    expect(label).not.toBe('Monitoring attached');
    expect(label).toBe('Monitoring not configured');
  });
});
