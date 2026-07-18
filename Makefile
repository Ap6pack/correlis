PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
PYTHONPATH := packages/correlis-schema/src:packages/correlis-ontology/src:packages/correlis-store/src:services/api/src

-include .env

export CORRELIS_DATABASE_URL
export CORRELIS_TEST_DATABASE_URL

.PHONY: install test test-unit test-postgres run lint clean bundle db-up db-down migrate

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -e packages/correlis-schema -e packages/correlis-ontology -e packages/correlis-store -e 'services/api[dev]'

test: test-unit


test-unit:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q -m "not postgres"


test-postgres:
	@if [ -z "$$CORRELIS_TEST_DATABASE_URL" ]; then echo "CORRELIS_TEST_DATABASE_URL is required for PostgreSQL integration tests"; exit 1; fi
	CORRELIS_DATABASE_URL=$$CORRELIS_TEST_DATABASE_URL PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q -m postgres

run:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m uvicorn correlis_api.app:app --host 0.0.0.0 --port 8080 --reload

lint:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m ruff check packages services tests

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
