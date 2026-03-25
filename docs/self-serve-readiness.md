# Decoda RWA Guard Self-Serve Readiness

## Implemented in code
- Auth lifecycle endpoints for signup/signin/signout, verify-email, resend verification, forgot/reset password.
- Multi-tenant workspace persistence with current workspace recovery and workspace switching.
- Workspace teammate primitives: invite create + invite accept + member listing.
- Billing foundation endpoint with workspace subscription bootstrap and Stripe-configured detection.
- Legal and support routes: `/privacy`, `/terms`, `/security`, `/support`.
- Migration `0003_enterprise_self_serve.sql` adds enterprise auth, invite, and subscription tables.

## Deployment-side configuration required
- Set `DATABASE_URL`, `AUTH_TOKEN_SECRET`, `LIVE_MODE_ENABLED=true`.
- Set `REQUIRE_EMAIL_VERIFICATION=true` for production.
- Run migrations (or set `RUN_MIGRATIONS_ON_STARTUP=true` temporarily).
- Configure Stripe secrets and price IDs for checkout/portal wiring:
  - `STRIPE_SECRET_KEY`
  - `STRIPE_WEBHOOK_SECRET`
  - `STRIPE_PRICE_STARTER`
  - `STRIPE_PRICE_ENTERPRISE`
- Configure transactional email provider wiring for verification/password-reset delivery.

## Live validation still required over time
- Production email delivery reliability (DKIM/SPF, suppression handling, bounce workflows).
- Stripe webhook reliability/idempotency under retry and race conditions.
- Audit log coverage verification against real customer activity.
- Pagination tuning and long-term query performance under tenant growth.
- Alerting and SLOs for auth, invite, billing, and history persistence failures.
