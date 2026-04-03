# Changelog

## 2026-04-03 — No-billing pilot launch polish

- Added a first-class no-billing pilot posture across marketing copy, settings UX, and readiness validation.
- Updated billing-disabled backend error payloads to include machine-readable `reason` (including `disabled_by_configuration`).
- Expanded public legal and trust surfaces: Terms, Privacy, Security, Support, and Trust pages.
- Aligned frontend and backend tests with no-billing pilot copy and behavior.
- Added reproducible operator commands: `npm run build:web`, `make validate-no-billing-launch`, and `npm run validate:no-billing-launch`.
- Updated launch/readiness docs to distinguish pilot-ready no-billing mode from future broad paid self-serve launch.
- Added Slack OAuth self-serve install endpoints (`/integrations/slack/oauth/start` + `/integrations/slack/oauth/callback`) with state TTL, secure token exchange, and workspace-scoped integration persistence.
- Added a strict paid-GA launch gate (`make validate-paid-ga`) that disallows skip statuses and enables strict billing runtime enforcement.
