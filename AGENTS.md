# AGENTS.md

## Repository coding standards
- Keep changes incremental and aligned with existing FastAPI + Next.js patterns.
- Prefer extending existing workflows over introducing new frameworks.
- For API changes:
  - Add SQL migrations under `services/api/migrations`.
  - Wire routes in `services/api/app/main.py` and implementation in `services/api/app/pilot.py`.
  - Add or update tests in `services/api/tests`.
- For frontend changes:
  - Place product pages under `apps/web/app/(product)`.
  - Keep authenticated fetches using `usePilotAuth().authHeaders()`.
  - Add or update Playwright source assertions in `apps/web/tests` when adding new flows.

## Product direction guardrails
- Maintain strict separation between demo/fallback behavior and workspace live data.
- Use self-serve language in product copy (avoid founder-led or manual-setup assumptions).
- When adding onboarding or admin workflows, ensure resumable state persists through API + database.
