import { spawnSync } from 'node:child_process';

const tests = [
  'tests/architecture-sections-conformance-source.spec.ts',
  'tests/detector-labels.spec.ts',
  'tests/risk-normalization-labels.spec.ts',
];
const result = spawnSync('npx', ['playwright', 'test', ...tests], {
  cwd: new URL('../apps/web', import.meta.url),
  stdio: 'inherit',
  shell: process.platform === 'win32',
});
process.exit(result.status ?? 1);
