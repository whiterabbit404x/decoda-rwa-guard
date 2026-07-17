#!/bin/sh
# docker-entrypoint.sh — resolve and exec the container's start command.
#
# Every Railway service in this project builds the SAME services/api/Dockerfile.
# Which process a given service runs is decided here, in strict priority order:
#
#   1. APP_START_COMMAND  — an explicit full command line; always wins (back-compat
#                           with the previous Dockerfile CMD that honoured it).
#   2. SERVICE_ROLE       — a role name mapped to the matching module command.
#   3. default            — uvicorn (the API) when neither is set.
#
# Why this exists: the previous CMD defaulted to uvicorn whenever APP_START_COMMAND
# was unset. A dedicated monitoring-worker service that was NOT pointed at
# railway-worker.json (and had no Custom Start Command / APP_START_COMMAND) therefore
# silently booted uvicorn / the API instead of the worker — its logs looked like
# API/QuickNode-webhook traffic and it NEVER emitted event=monitoring_worker_process_boot.
# The worker service already sets SERVICE_ROLE=worker (see docs/RAILWAY_DEPLOYMENT_GUIDE.md),
# so honouring SERVICE_ROLE here means that single env var is enough to run the correct
# process — the worker can no longer masquerade as the API.
#
# The resolved command is echoed as `event=container_start_command ...` BEFORE exec,
# so Railway logs prove — at the container level, before Python even starts — which
# process this service launched and why. This is the first line to grep for when a
# "worker" service is not producing worker logs.
#
# Railway's own Custom Start Command / config-as-code `startCommand` still override this
# entrypoint entirely (they replace the image CMD), so any explicitly-configured service
# is unaffected. No secrets are printed — only role names and the resolved command line.
set -eu

_default_api_command() {
    echo "uvicorn services.api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
}

resolve_start_command() {
    if [ -n "${APP_START_COMMAND:-}" ]; then
        _RESOLVED_SOURCE="APP_START_COMMAND"
        _RESOLVED_COMMAND="${APP_START_COMMAND}"
        return
    fi
    _RESOLVED_SOURCE="SERVICE_ROLE"
    case "${SERVICE_ROLE:-api}" in
        worker | monitoring-worker | monitoring_worker)
            _RESOLVED_COMMAND="python -m services.api.app.run_monitoring_worker" ;;
        ai-triage-worker | ai_triage_worker)
            _RESOLVED_COMMAND="python -m services.api.app.run_ai_triage_worker" ;;
        onboarding-worker | onboarding_worker)
            _RESOLVED_COMMAND="python -m services.api.app.run_onboarding_worker" ;;
        quicknode-live-worker | quicknode_live_worker)
            _RESOLVED_COMMAND="python -m services.api.app.run_quicknode_live_worker" ;;
        realtime-worker | realtime_worker)
            _RESOLVED_COMMAND="python -m services.api.app.run_realtime_worker" ;;
        recovery-drill-worker | recovery_drill_worker)
            _RESOLVED_COMMAND="python -m services.api.app.run_recovery_drill_worker" ;;
        retention-worker | retention_worker)
            _RESOLVED_COMMAND="python -m services.api.app.retention_worker" ;;
        api | web | "")
            _RESOLVED_COMMAND="$(_default_api_command)" ;;
        *)
            _RESOLVED_SOURCE="SERVICE_ROLE_unknown_defaulted_to_api"
            _RESOLVED_COMMAND="$(_default_api_command)" ;;
    esac
}

resolve_start_command
echo "event=container_start_command source=${_RESOLVED_SOURCE} service_role=${SERVICE_ROLE:-unset} command=${_RESOLVED_COMMAND}"

# Test/diagnostic hook: print the resolved command and exit without exec-ing it.
if [ "${CONTAINER_ENTRYPOINT_PRINT_ONLY:-}" = "1" ]; then
    exit 0
fi

exec sh -c "${_RESOLVED_COMMAND}"
