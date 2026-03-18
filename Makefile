.PHONY: up down logs install-python install-web init-local seed-all run-api run-risk run-oracle run-compliance run-reconciliation run-event-watcher run-backend run-web

PYTHONPATH_LOCAL := $(CURDIR)

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

install-python:
	python -m pip install -r requirements-local.txt

install-web:
	npm install --workspace apps/web

init-local:
	mkdir -p .data
	$(MAKE) seed-all

run-api:
	cd services/api && PYTHONPATH=$(PYTHONPATH_LOCAL) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8000

run-risk:
	cd services/risk-engine && PYTHONPATH=$(PYTHONPATH_LOCAL) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8001

run-oracle:
	cd services/oracle-service && PYTHONPATH=$(PYTHONPATH_LOCAL) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8002

run-compliance:
	cd services/compliance-service && PYTHONPATH=$(PYTHONPATH_LOCAL) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8003

run-reconciliation:
	cd services/reconciliation-service && PYTHONPATH=$(PYTHONPATH_LOCAL) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8004

run-event-watcher:
	cd services/event-watcher && PYTHONPATH=$(PYTHONPATH_LOCAL) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8005

run-backend:
	PYTHONPATH=$(PYTHONPATH_LOCAL) python scripts/run_local_backend.py

run-web:
	cd apps/web && npm run dev

seed-all:
	cd services/api && PYTHONPATH=$(PYTHONPATH_LOCAL) python scripts/seed.py
	cd services/risk-engine && PYTHONPATH=$(PYTHONPATH_LOCAL) python scripts/seed.py
	cd services/oracle-service && PYTHONPATH=$(PYTHONPATH_LOCAL) python scripts/seed.py
	cd services/compliance-service && PYTHONPATH=$(PYTHONPATH_LOCAL) python scripts/seed.py
	cd services/reconciliation-service && PYTHONPATH=$(PYTHONPATH_LOCAL) python scripts/seed.py
	cd services/event-watcher && PYTHONPATH=$(PYTHONPATH_LOCAL) python scripts/seed.py
