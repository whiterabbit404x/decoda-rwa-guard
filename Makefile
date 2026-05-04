.PHONY: up down logs install-python install-web install-web-test-runtime init-local seed-all run-api run-risk run-oracle run-compliance run-reconciliation run-event-watcher run-backend run-web run-web-smoke smoke-phase1 validate-production validate-staging validate-launch validate-no-billing-launch validate-paid-ga proof-no-billing-launch proof-feature1-live validate-feature1-live-artifacts validate-readiness-proof local-bootstrap-happy-path

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
	npx playwright install chromium

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

run-web-smoke:
	npm run start --workspace apps/web -- --hostname 127.0.0.1 --port 3000

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

validate-paid-ga:
	VALIDATION_MODE=paid_ga STRICT_PRODUCTION_BILLING=true python services/api/scripts/validate_staging.py

proof-no-billing-launch:
	python scripts/staging/run_no_billing_launch_proof.py

validate-feature1-live-artifacts:
	python services/api/scripts/validate_feature1_live_artifacts.py

proof-feature1-live:
	python services/api/scripts/run_feature1_live_proof.py
	$(MAKE) validate-feature1-live-artifacts

validate-readiness-proof:
	@bash -ec '\
		GUIDED_PROOF_ENV=staging; \
		ARTIFACT_DIR=services/api/artifacts/live_evidence/latest; \
		echo "[validate-readiness-proof] Running guided workflow in staging-safe simulator mode..."; \
		GUIDED_PROOF_ENV=$$GUIDED_PROOF_ENV EVIDENCE_SOURCE=guided_simulator TELEMETRY_EVIDENCE_SOURCE=guided_simulator EVM_RPC_URL=$${EVM_RPC_URL:-simulator} API_URL=$${API_URL:-http://localhost:8000} python services/api/scripts/run_live_evidence_flow.py; \
		echo "[validate-readiness-proof] Exporting readiness proof artifacts..."; \
		python services/api/scripts/export_live_proof_artifact_set.py; \
		echo "[validate-readiness-proof] Validating readiness proof summary..."; \
		python services/api/scripts/validate_readiness_proof.py --summary-path $$ARTIFACT_DIR/summary.json --environment $$GUIDED_PROOF_ENV || { \
			echo "ERROR: readiness proof validation failed. Review the readiness check report above for failed points." >&2; \
			exit 2; \
		}; \
		echo "[validate-readiness-proof] Readiness proof passed."; \
	'

local-bootstrap-happy-path:
	@echo "Step 1/5: migrate local Postgres"
	python services/api/scripts/migrate.py
	@echo "Step 2/5 (optional): seed Postgres pilot demo data for monitoring/auth workflows"
	@echo "python services/api/scripts/seed.py --pilot-demo"
	@echo "Step 3/5: run API"
	@echo "python scripts/run_service.py api --reload"
	@echo "Step 4/5: run monitoring worker"
	@echo "python -m services.api.app.run_monitoring_worker --worker-name local-monitor-worker --interval-seconds 15 --limit 50"
	@echo "Step 5/5: run web app"
	@echo "cd apps/web && npm run dev"
