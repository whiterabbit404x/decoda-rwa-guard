/**
 * Deployment Version — truthful web/API build identity for System Health.
 *
 * Pure-logic coverage for the always-rendered section plus the /api/build-info
 * route that supplies the web commit SHA. A missing SHA must always read as the
 * literal "unknown" message and never a fabricated value.
 */
import { expect, test } from '@playwright/test';

import {
  DEPLOYMENT_METADATA_UNAVAILABLE,
  buildDeploymentVersionFields,
  shortenCommitSha,
  type DeploymentVersionField,
} from '../app/(product)/system-health/_components/deployment-version';
import { getBuildInfo } from '../app/build-info';
import { GET as getBuildInfoRoute } from '../app/api/build-info/route';

const WEB_SHA = '7a285c27acedb17dd6a528112d4ad8c170a1a6a1';
const API_SHA = 'b1946ac92492d2347c6235b4d2611184abcdef01';

function byKey(fields: DeploymentVersionField[]) {
  return Object.fromEntries(fields.map((field) => [field.key, field]));
}

function withEnv(overrides: Record<string, string | undefined>, run: () => Promise<void> | void) {
  const original = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(overrides)) {
    original.set(key, process.env[key]);
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
  const restore = () => {
    for (const [key, value] of original.entries()) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  };
  try {
    const result = run();
    if (result instanceof Promise) {
      return result.finally(restore);
    }
    restore();
    return result;
  } catch (error) {
    restore();
    throw error;
  }
}

// ── Both SHAs available ───────────────────────────────────────────────────────

test('both SHAs available: web + API render the shortened SHA and keep the full value', () => {
  const fields = byKey(
    buildDeploymentVersionFields({
      webCommit: WEB_SHA,
      apiCommit: API_SHA,
      webBuildTime: '2026-07-24T15:31:30.000Z',
      apiDeployTime: '2026-07-24T15:40:00.000Z',
    }),
  );

  expect(fields['web-commit'].available).toBe(true);
  expect(fields['web-commit'].display).toBe('7a285c2');
  expect(fields['web-commit'].fullValue).toBe(WEB_SHA);

  expect(fields['api-commit'].available).toBe(true);
  expect(fields['api-commit'].display).toBe('b1946ac');
  expect(fields['api-commit'].fullValue).toBe(API_SHA);

  expect(fields['web-build-time'].display).toBe('2026-07-24T15:31:30.000Z');
  expect(fields['api-deploy-time'].display).toBe('2026-07-24T15:40:00.000Z');

  // Nothing is ever shown as the unavailable message when data is present.
  for (const field of Object.values(fields)) {
    expect(field.display).not.toBe(DEPLOYMENT_METADATA_UNAVAILABLE);
  }
});

// ── Web SHA missing ───────────────────────────────────────────────────────────

test('web SHA missing: web reads unknown, API commit stays truthful', () => {
  const fields = byKey(
    buildDeploymentVersionFields({ webCommit: null, apiCommit: API_SHA }),
  );

  expect(fields['web-commit'].available).toBe(false);
  expect(fields['web-commit'].display).toBe(DEPLOYMENT_METADATA_UNAVAILABLE);
  expect(fields['web-commit'].fullValue).toBeNull();

  // The other SHA must not be hidden or affected by the missing one.
  expect(fields['api-commit'].available).toBe(true);
  expect(fields['api-commit'].display).toBe('b1946ac');
});

test('web SHA blank/whitespace is treated as missing, never a fabricated empty SHA', () => {
  const fields = byKey(buildDeploymentVersionFields({ webCommit: '   ', apiCommit: API_SHA }));
  expect(fields['web-commit'].available).toBe(false);
  expect(fields['web-commit'].display).toBe(DEPLOYMENT_METADATA_UNAVAILABLE);
  expect(fields['web-commit'].fullValue).toBeNull();
});

// ── API SHA missing ───────────────────────────────────────────────────────────

test('API SHA missing (e.g. backend health unreachable): API reads unknown, web stays truthful', () => {
  const fields = byKey(
    buildDeploymentVersionFields({ webCommit: WEB_SHA, apiCommit: null }),
  );

  expect(fields['api-commit'].available).toBe(false);
  expect(fields['api-commit'].display).toBe(DEPLOYMENT_METADATA_UNAVAILABLE);
  expect(fields['api-commit'].fullValue).toBeNull();

  expect(fields['web-commit'].available).toBe(true);
  expect(fields['web-commit'].display).toBe('7a285c2');
});

// ── Truthful Unknown fallback ─────────────────────────────────────────────────

test('all metadata missing: every one of the four fields renders the truthful unknown message', () => {
  const fields = buildDeploymentVersionFields({});

  // Exactly the four required rows, always present (never hidden).
  expect(fields.map((field) => field.key)).toEqual([
    'web-commit',
    'api-commit',
    'web-build-time',
    'api-deploy-time',
  ]);

  for (const field of fields) {
    expect(field.available).toBe(false);
    expect(field.display).toBe(DEPLOYMENT_METADATA_UNAVAILABLE);
    expect(field.fullValue).toBeNull();
  }
});

test('shortenCommitSha returns null for missing input and 7 chars for a full SHA', () => {
  expect(shortenCommitSha(null)).toBeNull();
  expect(shortenCommitSha('')).toBeNull();
  expect(shortenCommitSha('   ')).toBeNull();
  expect(shortenCommitSha(WEB_SHA)).toBe('7a285c2');
  // A value already shorter than 7 chars is returned as-is (never padded/faked).
  expect(shortenCommitSha('abc')).toBe('abc');
});

// ── Build-info route response ─────────────────────────────────────────────────

test('build-info route response exposes the deployed web commit SHA (Vercel convention)', async () => {
  await withEnv(
    {
      RAILWAY_GIT_COMMIT_SHA: undefined,
      GIT_COMMIT_SHA: undefined,
      NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA: undefined,
      VERCEL_GIT_COMMIT_SHA: WEB_SHA,
    },
    async () => {
      const response = await getBuildInfoRoute(
        new Request('https://rwa.decodasecurity.example/api/build-info', {
          headers: { host: 'rwa.decodasecurity.example' },
        }),
      );
      const payload = (await response.json()) as Record<string, unknown>;

      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(payload.commitSha).toBe(WEB_SHA);
      expect(payload.shortCommitSha).toBe('7a285c2');
      expect(payload.host).toBe('rwa.decodasecurity.example');
    },
  );
});

test('build-info supports the repository GIT_COMMIT_SHA convention used by the Vercel build check', () => {
  const info = withEnv(
    {
      RAILWAY_GIT_COMMIT_SHA: undefined,
      VERCEL_GIT_COMMIT_SHA: undefined,
      NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA: undefined,
      GIT_COMMIT_SHA: WEB_SHA,
    },
    () => getBuildInfo(process.env),
  ) as ReturnType<typeof getBuildInfo>;

  expect(info.commitSha).toBe(WEB_SHA);
  expect(info.shortCommitSha).toBe('7a285c2');
});

test('build-info reports a missing web commit SHA truthfully as null (never fabricated)', () => {
  const info = getBuildInfo({ NODE_ENV: 'production' } as NodeJS.ProcessEnv, { host: 'app.example' });
  expect(info.commitSha).toBeNull();
  expect(info.shortCommitSha).toBeNull();
});
