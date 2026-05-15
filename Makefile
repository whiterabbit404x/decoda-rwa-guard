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
	@bash -euo pipefail -c '\
		GUIDED_PROOF_ENV=staging; \
		ARTIFACT_DIR=services/api/artifacts/live_evidence/latest; \
		echo "[validate-readiness-proof] Running guided workflow in controlled-pilot simulator-safe mode..."; \
		python services/api/scripts/generate_guided_simulator_readiness_bundle.py; \
		echo "[validate-readiness-proof] Generated services/api/artifacts/live_evidence/latest bundle."; \
		echo "[validate-readiness-proof] Validating readiness proof summary..."; \
		python services/api/scripts/validate_readiness_proof.py --summary-path $$ARTIFACT_DIR/summary.json --environment $$GUIDED_PROOF_ENV; \
		echo "[validate-readiness-proof] Readiness flags:"; \
		python services/api/scripts/print_readiness_flags.py --summary-path $$ARTIFACT_DIR/summary.json; \
		echo "[validate-readiness-proof] Verifying required artifacts are non-empty arrays..."; \
		python services/api/scripts/assert_readiness_artifacts_non_empty.py --artifacts-dir $$ARTIFACT_DIR; \
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


test-session4-backend:
	python -m pytest services/api/tests/test_detection_alert_incident_action_chain.py services/api/tests/test_monitoring_investigation_timeline.py -q

test-session4-web:
	cd apps/web && npm run test -- --grep "chain|incident|alert|proof"

verify-session4: test-session4-backend test-session4-web
