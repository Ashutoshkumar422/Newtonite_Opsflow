# Convenience targets. Every target is a thin wrapper around the commands documented in README.md.
.PHONY: help up up-sso idp guide down db-create install migrate seed seed-bulk api worker web test test-backend test-frontend lint verify e2e bench

PY := backend/.venv/bin/python
BACKEND := cd backend &&
VENV := . .venv/bin/activate &&

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

up:            ## Full stack in Docker → http://localhost:8080
	docker compose up --build

up-sso:        ## Same, plus the development SSO provider on :9000
	docker compose --env-file .env.sso.example --profile sso up --build

idp:           ## Development OIDC provider on :9000 (local, no Docker)
	$(BACKEND) $(VENV) python -m app.devtools.dev_oidc_provider

down:
	docker compose down

db-create:     ## Local Postgres: create app + test databases (uses psql as a superuser)
	psql -h localhost -U postgres -c "CREATE USER opsflow WITH PASSWORD 'opsflow' CREATEDB;" || true
	psql -h localhost -U postgres -c "CREATE DATABASE opsflow OWNER opsflow;" || true
	psql -h localhost -U postgres -c "CREATE DATABASE opsflow_test OWNER opsflow;" || true

install:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
	cd frontend && npm ci

migrate:
	$(BACKEND) $(VENV) alembic upgrade head

seed:
	$(BACKEND) $(VENV) python -m app.seed --reset

seed-bulk:     ## Add 50k synthetic items for scale testing
	$(BACKEND) $(VENV) python -m app.seed --bulk 50000

api:
	$(BACKEND) $(VENV) uvicorn app.main:app --reload --port 8000

worker:
	$(BACKEND) $(VENV) python -m app.worker

web:
	cd frontend && npm run dev

test: test-backend test-frontend

test-backend:
	$(BACKEND) $(VENV) pytest

test-frontend:
	cd frontend && npm test

lint:
	$(BACKEND) $(VENV) ruff check app tests scripts && ruff format --check app tests scripts && mypy app
	cd frontend && npm run typecheck && npm run lint

verify:
	$(BACKEND) $(VENV) python scripts/verify_setup.py

e2e:           ## Requires: running stack on a fresh seed + `npm i -D playwright && npx playwright install chromium`
	cd frontend && node e2e/smoke.mjs e2e-screenshots

guide:         ## Rebuild OpsFlow_Guide.pdf (needs reportlab + DejaVu fonts)
	$(BACKEND) $(VENV) cd .. && python docs/guide/build_guide.py

bench:         ## Requires seed-bulk first
	$(BACKEND) $(VENV) python scripts/benchmark.py
