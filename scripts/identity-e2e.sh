#!/usr/bin/env bash
# Shared-identity end-to-end run for RWA Guard: the real web BFF (AuthKit) and
# API in `GUARD_IDENTITY_MODE=workos` against a local MOCK of the WorkOS
# endpoints, a fresh Decoda platform schema (vendored migrations) and a fresh,
# fully migrated Guard schema, driven in a browser. Covers sign-in (PKCE +
# callback + exchange), HttpOnly-only sessions, tenant bootstrap, the
# product/organization switcher, organization switching (and its refusals),
# tenant isolation, entitlement withdrawal, WorkOS session revocation, the
# closed legacy paths and sign-out. No WorkOS credentials are involved.
#
#   IDENTITY_E2E_SERVER_URL   Postgres server (default postgresql://guard:guard@127.0.0.1:5432)
#   PYTHON                    (default .venv/bin/python if present, else python3)
#   IDENTITY_E2E_READER_PASSWORD  password of an existing decoda_platform_reader role
#   IDENTITY_E2E_SKIP_BUILD=1 reuse apps/web/.next from a previous run of this script
#   PLAYWRIGHT_CHROMIUM_PATH  optional Chromium executable for the driver
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="${IDENTITY_E2E_SERVER_URL:-postgresql://guard:guard@127.0.0.1:5432}"
GUARD_DB="$SERVER/guard_identity_e2e"
PLATFORM_DB="$SERVER/decoda_platform_guard_e2e"
MOCK_PORT=4556 API_PORT=8094 WEB_PORT=3194 REDIS_PORT=6394
# The production web build refuses loopback API hostnames (127.0.0.1, localhost —
# a deployment guard). 127.0.0.2 is loopback on Linux too, so the unchanged
# production build can reach the local API. (macOS: sudo ifconfig lo0 alias 127.0.0.2)
API_HOST=127.0.0.2
PYTHON="${PYTHON:-$([ -x "$ROOT/.venv/bin/python" ] && echo "$ROOT/.venv/bin/python" || echo python3)}"
LOG_DIR="${IDENTITY_E2E_LOG_DIR:-$ROOT/apps/web/.identity-e2e-logs}"
BFF_SECRET="identity-e2e-guard-bff-secret-0123456789abcdef"
CLIENT_ID="client_01GUARDE2E"
mkdir -p "$LOG_DIR"

kill_tree() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$child"; done
  kill "$1" 2>/dev/null || true
}
cleanup() {
  for pid in "${MOCK_PID:-}" "${API_PID:-}" "${WEB_PID:-}" "${REDIS_PID:-}"; do [ -n "$pid" ] && kill_tree "$pid"; done
  return 0
}
trap cleanup EXIT

for address in "127.0.0.1:$MOCK_PORT" "$API_HOST:$API_PORT" "127.0.0.1:$WEB_PORT" "127.0.0.1:$REDIS_PORT"; do
  if (exec 3<>"/dev/tcp/${address%:*}/${address#*:}") 2>/dev/null; then
    echo "✗ $address is already in use; stop the process listening on it and retry." >&2
    exit 1
  fi
done

echo "▶ fresh databases"
for db in guard_identity_e2e decoda_platform_guard_e2e; do
  psql "$SERVER/postgres" -q -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db"
done
for migration in "$ROOT"/services/api/migrations/*.sql; do
  PGOPTIONS='-c client_min_messages=warning' psql "$GUARD_DB" -q -v ON_ERROR_STOP=1 -f "$migration" > /dev/null
done
psql "$PLATFORM_DB" -q -v ON_ERROR_STOP=1 -f "$ROOT/services/api/tests/fixtures/decoda_platform_0001.sql"
psql "$PLATFORM_DB" -q -v ON_ERROR_STOP=1 -f "$ROOT/services/api/tests/fixtures/decoda_platform_0002.sql"
psql "$PLATFORM_DB" -q -v ON_ERROR_STOP=1 -f "$ROOT/apps/web/tests/identity-e2e/seed-platform.sql"
# Guard reads the platform through the least-privilege reader role when it
# exists (created here when the server allows it; set IDENTITY_E2E_READER_PASSWORD
# for an existing one), otherwise through the owner connection.
READER_PASSWORD="${IDENTITY_E2E_READER_PASSWORD:-identity-e2e-reader}"
psql "$SERVER/postgres" -q -c "DO \$\$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decoda_platform_reader') THEN CREATE ROLE decoda_platform_reader LOGIN PASSWORD '$READER_PASSWORD'; END IF; END\$\$" > /dev/null 2>&1 || true
READER_URL="$(echo "$SERVER" | sed -E "s#//[^@]+@#//decoda_platform_reader:$READER_PASSWORD@#")/decoda_platform_guard_e2e"
if psql "$PLATFORM_DB" -q -v ON_ERROR_STOP=1 -c "GRANT USAGE ON SCHEMA platform_api TO decoda_platform_reader" -c "GRANT SELECT ON ALL TABLES IN SCHEMA platform_api TO decoda_platform_reader" > /dev/null 2>&1 \
  && psql "$READER_URL" -q -c "SELECT 1 FROM platform_api.product_access_v1 LIMIT 1" > /dev/null 2>&1; then
  echo "  platform read through the decoda_platform_reader role"
else
  READER_URL="$PLATFORM_DB"
  echo "  (decoda_platform_reader unavailable; using the owner connection)"
fi

echo "▶ mock WorkOS on :$MOCK_PORT"
MOCK_WORKOS_PORT=$MOCK_PORT MOCK_CLIENT_ID=$CLIENT_ID node "$ROOT/apps/web/tests/identity-e2e/mock-workos.mjs" > "$LOG_DIR/mock.log" 2>&1 &
MOCK_PID=$!

echo "▶ Redis on :$REDIS_PORT (rate limiting + alert streams; production requires it)"
redis-server --port "$REDIS_PORT" --bind 127.0.0.1 --save '' --appendonly no > "$LOG_DIR/redis.log" 2>&1 &
REDIS_PID=$!

# The API runs as in production (APP_ENV=production): the same fail-closed
# startup validation, with throwaway keys generated for this run. No e-mail is
# sent by these flows; the Resend key is a placeholder that is never used.
echo "▶ API on :$API_PORT (APP_ENV=production, GUARD_IDENTITY_MODE=workos)"
cd "$ROOT"
env APP_ENV=production LIVE_MODE_ENABLED=true DATABASE_URL="$GUARD_DB" REDIS_URL="redis://127.0.0.1:$REDIS_PORT/0" \
  AUTH_TOKEN_SECRET="$(openssl rand -hex 32)" SECRET_ENCRYPTION_KEY="$(openssl rand -base64 32)" EXPORT_SIGNING_SECRET="$(openssl rand -hex 32)" \
  EMAIL_PROVIDER=resend EMAIL_RESEND_API_KEY=re_identity_e2e_placeholder EMAIL_FROM=no-reply@identity-e2e.test \
  LIVE_MONITORING_ENABLED=false WORKER_ENABLED=false \
  GUARD_IDENTITY_MODE=workos WORKOS_CLIENT_ID=$CLIENT_ID WORKOS_API_KEY=sk_test_identity_e2e_not_a_key \
  WORKOS_BASE_URL="http://127.0.0.1:$MOCK_PORT" WORKOS_ISSUER="https://api.workos.test/user_management/$CLIENT_ID" \
  DECODA_PLATFORM_DATABASE_URL="$READER_URL" DECODA_IDP_MFA_REQUIRED=true GUARD_BFF_SHARED_SECRET="$BFF_SECRET" \
  ALLOWED_ORIGINS="http://127.0.0.1:$WEB_PORT" PYTHONPATH="$ROOT" \
  "$PYTHON" -m uvicorn services.api.app.main:app --host "$API_HOST" --port "$API_PORT" --log-level warning > "$LOG_DIR/api.log" 2>&1 &
API_PID=$!

cd "$ROOT/apps/web"
WEB_ENV=(GUARD_IDENTITY_MODE=workos API_URL="http://$API_HOST:$API_PORT" LIVE_MODE_ENABLED=true NEXT_PUBLIC_LIVE_MODE_ENABLED=true
  GUARD_BFF_SHARED_SECRET="$BFF_SECRET" WORKOS_CLIENT_ID=$CLIENT_ID WORKOS_API_KEY=sk_test_identity_e2e_not_a_key
  WORKOS_COOKIE_PASSWORD=identity-e2e-cookie-password-0123456789abcdef
  NEXT_PUBLIC_WORKOS_REDIRECT_URI="http://127.0.0.1:$WEB_PORT/auth/callback"
  WORKOS_API_HOSTNAME=127.0.0.1 WORKOS_API_HTTPS=false WORKOS_API_PORT=$MOCK_PORT
  DECODA_WEBSITE_URL=https://www.decodasecurity.com DECODA_VAULT_URL=https://vault.decodasecurity.com
  VERCEL_ENV=development)
# A production build and server (NODE_ENV=production). VERCEL_ENV=development
# tells next.config.js's deployment check (run by build AND start) that this is
# not a Vercel preview/production deployment, where a loopback API_URL is refused.
if [ "${IDENTITY_E2E_SKIP_BUILD:-}" = 1 ] && [ -f .next/BUILD_ID ]; then
  echo "▶ reusing the existing web build (IDENTITY_E2E_SKIP_BUILD=1)"
else
  echo "▶ building web app (NEXT_PUBLIC_WORKOS_REDIRECT_URI is inlined at build time)"
  env "${WEB_ENV[@]}" NEXT_TELEMETRY_DISABLED=1 npx next build > "$LOG_DIR/build.log" 2>&1
fi
echo "▶ web on :$WEB_PORT (GUARD_IDENTITY_MODE=workos)"
env "${WEB_ENV[@]}" npx next start --port "$WEB_PORT" > "$LOG_DIR/web.log" 2>&1 &
WEB_PID=$!

ready=0
for _ in $(seq 1 120); do
  for pid in "$MOCK_PID" "$API_PID" "$WEB_PID" "$REDIS_PID"; do kill -0 "$pid" 2>/dev/null || { echo "✗ a server exited; see $LOG_DIR" >&2; exit 1; }; done
  if curl -sf "http://$API_HOST:$API_PORT/health" >/dev/null && curl -s -o /dev/null "http://127.0.0.1:$WEB_PORT/sign-in"; then ready=1; break; fi
  sleep 0.5
done
[ "$ready" = 1 ] || { echo "✗ stack did not become ready; see $LOG_DIR" >&2; exit 1; }

echo "▶ driving the browser"
mkdir -p "$LOG_DIR/shots"
IDENTITY_E2E_WEB_URL="http://127.0.0.1:$WEB_PORT" IDENTITY_E2E_API_URL="http://$API_HOST:$API_PORT" IDENTITY_E2E_MOCK_URL="http://127.0.0.1:$MOCK_PORT" \
  IDENTITY_E2E_PLATFORM_DB_URL="$PLATFORM_DB" IDENTITY_E2E_GUARD_DB_URL="$GUARD_DB" IDENTITY_E2E_SHOTS="$LOG_DIR/shots/" \
  node tests/identity-e2e/drive.mjs
