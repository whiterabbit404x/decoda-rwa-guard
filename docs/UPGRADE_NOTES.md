# Upgrade Notes

## Frontend and test tooling
- Added explicit `eslint-config-next` in `apps/web` dev dependencies.
- Bumped Playwright packages in root workspace.

## Manual deploy implications
- Re-run install in CI and deployments to refresh lockfile and browser binaries.
- If Playwright browsers are cached, run `npx playwright install --with-deps` in CI image prep.
