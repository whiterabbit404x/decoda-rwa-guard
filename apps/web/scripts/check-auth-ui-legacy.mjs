import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const repoRoot = process.cwd();
const appRoot = path.join(repoRoot, 'apps/web/app');
const legacyLabel = 'Auth environment snapshot';
const authUiPattern = /apps\/web\/app\/(auth-.*\.tsx|preview-deployment-notice\.tsx|sign-in\/.*\.tsx|sign-up\/.*\.tsx)$/;
const failures = [];

function visit(directory) {
  for (const entry of readdirSync(directory)) {
    const fullPath = path.join(directory, entry);
    const stats = statSync(fullPath);

    if (stats.isDirectory()) {
      if (entry === '.next' || entry === 'node_modules') {
        continue;
      }
      visit(fullPath);
      continue;
    }

    if (!fullPath.endsWith('.ts') && !fullPath.endsWith('.tsx')) {
      continue;
    }

    const relativePath = path.relative(repoRoot, fullPath).replace(/\\/g, '/');
    const source = readFileSync(fullPath, 'utf8');

    if (source.includes(legacyLabel)) {
      failures.push(`${relativePath}: contains legacy label \"${legacyLabel}\"`);
    }

    if (authUiPattern.test(relativePath) && source.includes('NEXT_PUBLIC_API_URL')) {
      failures.push(`${relativePath}: renders raw NEXT_PUBLIC_API_URL wording in auth UI`);
    }
  }
}

visit(appRoot);

if (failures.length > 0) {
  console.error('Legacy auth UI guardrails failed:');
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log('Legacy auth UI guardrails passed.');
