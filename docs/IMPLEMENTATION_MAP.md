# Implementation Map

## auth/session
- `apps/web/app/pilot-auth-context.tsx`
- `apps/web/app/api/auth/_shared/proxy.ts`
- `apps/web/app/api/auth/signin/route.ts`
- `apps/web/app/api/auth/signup/route.ts`
- `apps/web/app/api/auth/mfa/complete-signin/route.ts`
- `apps/web/app/api/auth/signout/route.ts`
- `apps/web/app/api/auth/signout-all/route.ts`
- `apps/web/app/api/auth/csrf/route.ts`

## secret encryption
- `services/api/app/secret_crypto.py`
- `services/api/app/pilot.py`
- `services/api/migrations/0018_security_storage_hardening.sql`
- `services/api/tests/test_secret_crypto.py`

## export storage
- `services/api/app/export_storage.py`
- `services/api/app/pilot.py`
- `services/api/app/main.py`
- `services/api/migrations/0018_security_storage_hardening.sql`

## copy cleanup
- `apps/web/app/page.tsx`
- `apps/web/app/pilot-mode-banner.tsx`
- `docs/PUBLIC_COPY_REVIEW.md`

## dependency upgrades
- `package.json`
- `apps/web/package.json`
- `services/api/requirements.txt`
- `docs/UPGRADE_NOTES.md`

## staging evidence
- `apps/web/tests/staging-evidence-flow.spec.ts`
- `scripts/staging/run_evidence_flow.py`
- `evidence/`
- `docs/STAGING_EVIDENCE_TEMPLATE.md`
- `docs/REAL_RUN_CHECKLIST.md`
