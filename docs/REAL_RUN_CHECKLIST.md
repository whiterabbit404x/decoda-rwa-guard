# Real Run Checklist

- [ ] Run `npm run proof:no-billing-launch` and capture `artifacts/launch-proof/<timestamp>/summary.md`
- [ ] `STAGING_BASE_URL` set
- [ ] `STAGING_EMAIL_INBOX_PROVIDER` configured
- [ ] Verification-link capture configured
- [ ] `STAGING_SLACK_WEBHOOK_URL` or bot credentials configured
- [ ] S3 export storage configured
- [ ] `SECRET_ENCRYPTION_KEY` configured
- [ ] Run `python scripts/staging/run_evidence_flow.py` (or let `proof:no-billing-launch` run it automatically when `STAGING_*` is set)
