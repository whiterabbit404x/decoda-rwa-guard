FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY services/api/requirements.txt services/api/requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r services/api/requirements.txt

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

CMD ["sh", "-c", "uvicorn services.api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
