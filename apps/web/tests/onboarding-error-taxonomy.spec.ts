/**
 * Behavioral unit tests for the Onboarding "Failed to fetch" fix — the customer-safe
 * error taxonomy. These import the real pure helpers (no server/browser needed) and
 * prove that:
 *   - a browser-level transport failure never leaks the raw "Failed to fetch" string,
 *   - each structured backend code maps to an actionable, fail-closed message,
 *   - a normal EOA (wallet) is steered to Monitoring Sources, not shown as healthy,
 *   - a missing RPC provider is steered to Integrations,
 *   - unknown codes never surface raw JSON, HTML, or tracebacks.
 */
import { expect, test } from '@playwright/test';
import {
  describeOnboardingError,
  isTransportError,
  OnboardingRequestError,
  ONBOARDING_TRANSPORT_MESSAGE,
  MONITORING_SOURCES_ROUTE,
  INTEGRATIONS_ROUTE,
} from '../app/onboarding-agent-client';

test.describe('transport failures are converted to a safe message', () => {
  test('isTransportError detects the "Failed to fetch" TypeError', () => {
    expect(isTransportError(new TypeError('Failed to fetch'))).toBe(true);
    expect(isTransportError(new TypeError('Load failed'))).toBe(true);
    expect(isTransportError(new Error('NetworkError when attempting to fetch resource.'))).toBe(true);
  });

  test('a normal domain error is not treated as a transport failure', () => {
    expect(isTransportError(new OnboardingRequestError(describeOnboardingError('zero_address')))).toBe(false);
    expect(isTransportError(new Error('No deployed contract.'))).toBe(false);
  });

  test('backend_unreachable maps to the safe transport message and stays recoverable', () => {
    const info = describeOnboardingError('backend_unreachable');
    expect(info.message).toBe(ONBOARDING_TRANSPORT_MESSAGE);
    expect(info.recoverable).toBe(true);
    // The customer must never see the raw browser string.
    expect(info.message.toLowerCase()).not.toContain('failed to fetch');
  });
});

test.describe('structured backend codes map to actionable messages', () => {
  test('invalid address format', () => {
    const info = describeOnboardingError('invalid_address_format');
    expect(info.code).toBe('INVALID_ADDRESS');
    expect(info.recoverable).toBe(true);
    expect(info.message).toMatch(/valid.*address/i);
  });

  test('zero address', () => {
    const info = describeOnboardingError('zero_address');
    expect(info.code).toBe('ZERO_ADDRESS');
    expect(info.message).toMatch(/zero address/i);
  });

  test('EOA (no deployed bytecode) suggests wallet monitoring via Monitoring Sources', () => {
    const info = describeOnboardingError(
      'no_deployed_contract',
      'No deployed bytecode found at this address (it appears to be an externally owned account).',
    );
    expect(info.code).toBe('NO_CONTRACT_BYTECODE');
    expect(info.message).toMatch(/wallet/i);
    expect(info.message).toMatch(/Monitoring Sources/i);
    expect(info.suggestion?.href).toBe(MONITORING_SOURCES_ROUTE);
  });

  test('missing RPC provider suggests configuring one in Integrations', () => {
    const info = describeOnboardingError('no_rpc_endpoint');
    expect(info.code).toBe('RPC_NOT_CONFIGURED');
    expect(info.recoverable).toBe(true);
    expect(info.suggestion?.href).toBe(INTEGRATIONS_ROUTE);
  });

  test('RPC chain mismatch explains the wrong-network case', () => {
    expect(describeOnboardingError('chain_mismatch').code).toBe('RPC_CHAIN_MISMATCH');
    expect(describeOnboardingError('rpc_chain_mismatch').code).toBe('RPC_CHAIN_MISMATCH');
    expect(describeOnboardingError('chain_mismatch').message).toMatch(/network/i);
  });

  test('RPC unavailable is recoverable', () => {
    expect(describeOnboardingError('rpc_unreachable').code).toBe('RPC_UNAVAILABLE');
    expect(describeOnboardingError('rpc_unreachable').recoverable).toBe(true);
  });

  test('authentication required maps to the session-expired message', () => {
    const info = describeOnboardingError('unauthenticated');
    expect(info.code).toBe('AUTHENTICATION_REQUIRED');
    expect(info.message).toMatch(/session has expired/i);
  });

  test('cross-workspace denial is not recoverable by retry', () => {
    const info = describeOnboardingError('workspace_access_denied');
    expect(info.code).toBe('WORKSPACE_ACCESS_DENIED');
    expect(info.recoverable).toBe(false);
  });
});

test.describe('unknown / unsafe backend detail never leaks to the customer', () => {
  test('a JSON blob detail is dropped in favour of a generic safe message', () => {
    const info = describeOnboardingError('some_new_code', '{"stack":"secret","detail":"boom"}');
    expect(info.message.startsWith('{')).toBe(false);
    expect(info.message).not.toContain('secret');
  });

  test('a Python traceback detail is dropped', () => {
    const info = describeOnboardingError(null, 'Traceback (most recent call last): ... KeyError');
    expect(info.message.toLowerCase()).not.toContain('traceback');
    expect(info.recoverable).toBe(true);
  });

  test('an HTML error document detail is dropped', () => {
    const info = describeOnboardingError('internal_error', '<!DOCTYPE html><html>502 Bad Gateway</html>');
    expect(info.message.startsWith('<')).toBe(false);
  });

  test('a safe backend sentence for an unknown code is surfaced verbatim', () => {
    const info = describeOnboardingError('some_new_code', 'The provider rejected the request.');
    expect(info.message).toBe('The provider rejected the request.');
  });
});

test.describe('OnboardingRequestError carries structured info', () => {
  test('info + silent flag are preserved', () => {
    const err = new OnboardingRequestError(describeOnboardingError('unauthenticated'), true);
    expect(err).toBeInstanceOf(Error);
    expect(err.info.code).toBe('AUTHENTICATION_REQUIRED');
    expect(err.silent).toBe(true);
    expect(err.message).toBe(err.info.message);
  });
});
