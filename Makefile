.PHONY: up down logs run-api run-risk run-oracle run-compliance run-reconciliation run-event-watcher run-web seed-all

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

run-api:
	cd services/api && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run-risk:
	cd services/risk-engine && uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

run-oracle:
	cd services/oracle-service && uvicorn app.main:app --reload --host 0.0.0.0 --port 8002

run-compliance:
	cd services/compliance-service && uvicorn app.main:app --reload --host 0.0.0.0 --port 8003

run-reconciliation:
	cd services/reconciliation-service && uvicorn app.main:app --reload --host 0.0.0.0 --port 8004

run-event-watcher:
	cd services/event-watcher && uvicorn app.main:app --reload --host 0.0.0.0 --port 8005

run-web:
	cd apps/web && npm run dev

seed-all:
	python services/api/scripts/seed.py
	python services/risk-engine/scripts/seed.py
	python services/oracle-service/scripts/seed.py
	python services/compliance-service/scripts/seed.py
	python services/reconciliation-service/scripts/seed.py
	python services/event-watcher/scripts/seed.py
