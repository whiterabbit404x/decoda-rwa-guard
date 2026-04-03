.PHONY: up down logs install-python install-web install-web-test-runtime init-local seed-all run-api run-risk run-oracle run-compliance run-reconciliation run-event-watcher run-backend run-web smoke-phase1 validate-production validate-staging validate-launch validate-no-billing-launch proof-no-billing-launch

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

install-python:
	python -m pip install -r requirements-local.txt

install-web:
	npm ci

install-web-test-runtime:
	npm ci
	npm run bootstrap:e2e

init-local:
	mkdir -p .data
	$(MAKE) seed-all

run-api:
	python scripts/run_service.py api --reload

run-risk:
	python scripts/run_service.py risk-engine --reload

run-oracle:
	cd services/oracle-service && PYTHONPATH=$(CURDIR) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8002

run-compliance:
	cd services/compliance-service && PYTHONPATH=$(CURDIR) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8003

run-reconciliation:
	cd services/reconciliation-service && PYTHONPATH=$(CURDIR) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8004

run-event-watcher:
	cd services/event-watcher && PYTHONPATH=$(CURDIR) uvicorn app.main:app --env-file .env --reload --host 0.0.0.0 --port 8005

run-backend:
	python scripts/run_local_backend.py

run-web:
	cd apps/web && npm run dev

seed-all:
	cd services/api && PYTHONPATH=$(CURDIR) python scripts/seed.py
	cd services/risk-engine && PYTHONPATH=$(CURDIR) python scripts/seed.py
	cd services/oracle-service && PYTHONPATH=$(CURDIR) python scripts/seed.py
	cd services/compliance-service && PYTHONPATH=$(CURDIR) python scripts/seed.py
	cd services/reconciliation-service && PYTHONPATH=$(CURDIR) python scripts/seed.py
	cd services/event-watcher && PYTHONPATH=$(CURDIR) python scripts/seed.py

smoke-phase1:
	python scripts/smoke_phase1.py

validate-production:
	python services/api/scripts/validate_production_readiness.py


validate-staging:
	python services/api/scripts/validate_staging.py

validate-launch:
	$(MAKE) validate-production
	$(MAKE) validate-staging

validate-no-billing-launch:
	BILLING_PROVIDER=none VALIDATION_MODE=no_billing_pilot python services/api/scripts/validate_staging.py

proof-no-billing-launch:
	python scripts/staging/run_no_billing_launch_proof.py
