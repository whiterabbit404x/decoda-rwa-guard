# Real Run Checklist

1. Set required staging credentials and URLs.
2. Run `scripts/staging_evidence/run_evidence.sh`.
3. Execute Playwright staging flow with real inbox verification wiring.
4. Execute backend smoke checks for monitoring, exports, and integrations.
5. Attach artifacts from `artifacts/staging-evidence/<RUN_ID>/`.
6. Record pass/fail and remediation actions.
