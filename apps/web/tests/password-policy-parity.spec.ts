/**
 * The password rules the reset screen shows must be the rules the API enforces.
 *
 * A checklist that promises a stricter policy than the backend applies pushes users
 * into needless complexity; one that promises a looser policy lets a form look
 * satisfied and then be rejected. Both are the same defect — the UI stating something
 * the system does not do — so this spec reads the Python enforcement directly and
 * fails when `app/password-policy.ts` drifts from it.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import {
  PASSWORD_MIN_LENGTH,
  PASSWORD_REQUIREMENTS,
  describePasswordStrength,
  evaluatePasswordRequirements,
  isPasswordAcceptable,
  validateNewPassword,
} from '../app/password-policy';

const pilotSource = readFileSync(
  path.resolve(__dirname, '../../../services/api/app/pilot.py'),
  'utf8',
);
const policySource = readFileSync(path.resolve(__dirname, '../app/password-policy.ts'), 'utf8');
const requirementsSource = readFileSync(
  path.resolve(__dirname, '../app/reset-password/components/password-requirements.tsx'),
  'utf8',
);

test.describe('the displayed policy matches the enforced policy', () => {
  test('the minimum length mirrors PASSWORD_MIN_LENGTH in the api', () => {
    const declared = pilotSource.match(/^PASSWORD_MIN_LENGTH = (\d+)$/m);

    expect(declared, 'PASSWORD_MIN_LENGTH not found in services/api/app/pilot.py').not.toBeNull();
    expect(PASSWORD_MIN_LENGTH).toBe(Number(declared?.[1]));
    // And the constant is what enforcement actually compares against.
    expect(pilotSource).toContain('if len(password) < PASSWORD_MIN_LENGTH:');
  });

  test('the character-class rules mirror _require_strong_password', () => {
    const enforcement = pilotSource.slice(
      pilotSource.indexOf('def _require_strong_password'),
      pilotSource.indexOf('def _store_session'),
    );

    expect(enforcement).toContain("re.search(r'[A-Z]', password)");
    expect(enforcement).toContain("re.search(r'[a-z]', password)");
    expect(enforcement).toContain("re.search(r'\\d', password)");

    const rules = PASSWORD_REQUIREMENTS.map((requirement) => requirement.id).sort();
    expect(rules).toEqual(['lower_and_upper', 'min_length', 'number']);
  });

  test('the ui invents no rule the api does not enforce', () => {
    // Notably: no special-character rule. The API accepts a password without one,
    // so a checklist demanding one would be telling the user something untrue.
    const enforcement = pilotSource.slice(
      pilotSource.indexOf('def _require_strong_password'),
      pilotSource.indexOf('def _store_session'),
    );
    expect(enforcement).not.toMatch(/[!@#$%^&*]/);

    for (const source of [policySource, requirementsSource]) {
      expect(source).not.toMatch(/special character/i);
    }

    expect(isPasswordAcceptable('NoSymbols12')).toBe(true);
    expect(evaluatePasswordRequirements('NoSymbols12').every((rule) => rule.met)).toBe(true);
  });

  test('the api publishes the same rule ids and labels it enforces', () => {
    // PASSWORD_POLICY_RULES is what pilot.password_policy() hands a client at runtime.
    // Its ids and labels are the ones this module mirrors, so they are pinned here in
    // the exact form the Python source declares them.
    const declared = pilotSource.slice(
      pilotSource.indexOf('PASSWORD_POLICY_RULES'),
      pilotSource.indexOf('SESSION_TTL_HOURS'),
    );

    expect(declared).toContain("{'id': 'min_length', 'label': f'At least {PASSWORD_MIN_LENGTH} characters'}");
    expect(declared).toContain("{'id': 'lower_and_upper', 'label': 'Uppercase & lowercase letters'}");
    expect(declared).toContain("{'id': 'number', 'label': 'One number'}");

    // Same ids, same order, and the same rendered labels on this side.
    expect(PASSWORD_REQUIREMENTS.map((requirement) => requirement.id)).toEqual([
      'min_length',
      'lower_and_upper',
      'number',
    ]);
    expect(PASSWORD_REQUIREMENTS.map((requirement) => requirement.label)).toEqual([
      `At least ${PASSWORD_MIN_LENGTH} characters`,
      'Uppercase & lowercase letters',
      'One number',
    ]);
  });
});

test.describe('policy evaluation', () => {
  test('a password is acceptable only when every rule passes', () => {
    expect(isPasswordAcceptable('NewPassword123')).toBe(true);

    const rejected = [
      '',
      'short1A',            // too short
      'alllowercase123',    // no upper case
      'ALLUPPERCASE123',    // no lower case
      'NoDigitsAtAllHere',  // no number
    ];
    for (const password of rejected) {
      expect(isPasswordAcceptable(password), password).toBe(false);
    }
  });

  test('each rule reports its own pass/fail so the user can see what is missing', () => {
    const states = evaluatePasswordRequirements('alllowercase123');
    const byId = Object.fromEntries(states.map((state) => [state.id, state.met]));

    expect(byId.min_length).toBe(true);
    expect(byId.number).toBe(true);
    expect(byId.lower_and_upper).toBe(false);
  });

  test('strength is progress against the real policy, never a flattering guess', () => {
    expect(describePasswordStrength('').label).toBe('Too weak');
    expect(describePasswordStrength('abc').score).toBe(0);

    const strong = describePasswordStrength('NewPassword123');
    expect(strong.label).toBe('Strong');
    expect(strong.score).toBe(strong.total);

    // Anything the API would reject must not be labelled Strong.
    for (const password of ['short1A', 'alllowercase123', 'ALLUPPERCASE123', 'NoDigitsAtAllHere']) {
      expect(describePasswordStrength(password).label, password).not.toBe('Strong');
    }
  });
});

test.describe('the new-password form validates before it submits', () => {
  test('a missing password is reported before anything else', () => {
    expect(validateNewPassword('', '')).toBe('Enter a new password.');
  });

  test('a policy failure is reported against the checklist, not as a mismatch', () => {
    expect(validateNewPassword('weak', 'weak')).toBe(
      'Your password does not meet all of the requirements below.',
    );
  });

  test('a missing confirmation asks for one', () => {
    expect(validateNewPassword('NewPassword123', '')).toBe('Re-enter your new password to confirm it.');
  });

  test('mismatched passwords are refused', () => {
    expect(validateNewPassword('NewPassword123', 'NewPassword124')).toBe('Both passwords must match.');
    // Case and whitespace differences are mismatches, not near-misses.
    expect(validateNewPassword('NewPassword123', 'newpassword123')).toBe('Both passwords must match.');
    expect(validateNewPassword('NewPassword123', 'NewPassword123 ')).toBe('Both passwords must match.');
  });

  test('a matching, policy-satisfying password produces no error', () => {
    expect(validateNewPassword('NewPassword123', 'NewPassword123')).toBe('');
  });
});
