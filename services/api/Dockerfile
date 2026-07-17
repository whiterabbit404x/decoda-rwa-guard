FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY services/api/requirements.txt services/api/requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r services/api/requirements.txt \
    && useradd --system --create-home --uid 1001 --shell /sbin/nologin app

# Railway must build this Dockerfile from the repository root so these sibling
# service fixture directories remain available during COPY.
COPY phase1_local ./phase1_local
COPY services/api ./services/api
COPY services/risk-engine/app ./services/risk-engine/app
COPY services/risk-engine/data ./services/risk-engine/data
COPY services/threat-engine/app ./services/threat-engine/app
COPY services/threat-engine/data ./services/threat-engine/data
COPY services/compliance-service/app ./services/compliance-service/app
COPY services/compliance-service/data ./services/compliance-service/data
COPY services/reconciliation-service/app ./services/reconciliation-service/app
COPY services/reconciliation-service/data ./services/reconciliation-service/data

USER app

# Dispatch the start command from SERVICE_ROLE (falling back to APP_START_COMMAND, then
# uvicorn/the API). This stops a worker service that was never pointed at its config-as-code
# file from silently booting the API — see services/api/docker-entrypoint.sh. Railway's own
# Custom Start Command / config-as-code startCommand still override this CMD when set.
CMD ["sh", "/app/services/api/docker-entrypoint.sh"]
