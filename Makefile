PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
PYTHONPATH := packages/correlis-schema/src:packages/correlis-ontology/src:packages/correlis-store/src:services/api/src
ENV_FILE := .env

ifneq ($(wildcard $(ENV_FILE)),)
include $(ENV_FILE)
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(ENV_FILE))
endif

CORRELIS_HOST ?= 0.0.0.0
CORRELIS_PORT ?= 8080
CORRELIS_BASE_URL ?= http://localhost:$(CORRELIS_PORT)

.PHONY: install local-setup local-db doctor test test-unit test-postgres test-all run smoke lint clean bundle db-up db-down migrate

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --quiet -e packages/correlis-schema -e packages/correlis-ontology -e packages/correlis-store -e 'services/api[dev]'

local-setup: install
	PYTHONPATH=$(PYTHONPATH) $(PY) scripts/bootstrap_local.py

local-db: db-up
	@docker compose exec -T postgres sh -c 'until pg_isready -U correlis -d correlis >/dev/null 2>&1; do sleep 1; done'
	@docker compose exec -T postgres createdb -U correlis correlis_test >/dev/null 2>&1 || true
	$(MAKE) migrate


doctor:
	@$(PYTHON) -c 'import sys; print(sys.version); assert sys.version_info >= (3, 11), "Correlis requires Python 3.11+"'
	@docker --version
	@docker compose version
	@test -f .env || (echo '.env is missing; run make local-setup' && exit 1)
	@test -x $(PY) || (echo '.venv is missing; run make local-setup' && exit 1)
	@echo 'Local development prerequisites look available.'


test: test-unit


test-unit:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q -m "not postgres"


test-postgres:
	@if [ -z "$$CORRELIS_TEST_DATABASE_URL" ]; then echo "CORRELIS_TEST_DATABASE_URL is required for PostgreSQL integration tests"; exit 1; fi
	CORRELIS_DATABASE_URL=$$CORRELIS_TEST_DATABASE_URL PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q -m postgres


test-all: test test-postgres


run:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m uvicorn correlis_api.app:app --host $(CORRELIS_HOST) --port $(CORRELIS_PORT) --reload


smoke:
	CORRELIS_BASE_URL=$(CORRELIS_BASE_URL) PYTHONPATH=$(PYTHONPATH) $(PY) scripts/local_smoke.py


lint:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m ruff check packages services tests scripts


clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .coverage htmlcov


bundle:
	git bundle create correlis.bundle --all



db-up:
	docker compose up -d postgres


db-down:
	docker compose down


migrate:
	@if [ -z "$$CORRELIS_DATABASE_URL" ]; then echo "CORRELIS_DATABASE_URL is required"; exit 1; fi
	PYTHONPATH=$(PYTHONPATH) $(PY) -m alembic upgrade head
