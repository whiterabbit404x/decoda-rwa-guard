# Self-Serve SaaS Release Checklist

## Required configuration
- API: `DATABASE_URL`, `AUTH_TOKEN_SECRET`, `APP_BASE_URL`.
- Email: `MAIL_PROVIDER_API_KEY`, `MAIL_FROM`, optional `MAIL_PROVIDER_API_URL`.
- Billing: `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`.
- Web: `API_URL` (or `NEXT_PUBLIC_API_URL`) and matching public URL callbacks.

## Deploy readiness checks
1. Run DB migrations, including `0003_self_serve_foundation.sql`.
2. Verify `/health` and `/health/details` succeed.
3. Verify auth lifecycle smoke path: signup → verify-email → signin → dashboard → invite → billing → signout → signin.
4. Verify logs include request/action metadata for auth, invite, and billing events.
5. Confirm no secrets are printed in logs or shown in UI diagnostics.

## Live observation after deploy
- Monitor 4xx/5xx rates for `/auth/*`, `/workspaces/invites*`, `/billing/*`.
- Monitor expired/invalid token frequency and signin failures.
- Monitor email delivery failures and Stripe checkout/provider errors.
- Validate workspace hydration and invite acceptance success rates for multi-org users.
