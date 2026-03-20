FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/api/requirements.txt services/api/requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r services/api/requirements.txt

COPY phase1_local ./phase1_local
COPY services/api ./services/api

CMD ["sh", "-c", "cd services/api && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
