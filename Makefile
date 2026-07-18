PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
PYTHONPATH := packages/correlis-schema/src:packages/correlis-store/src:services/api/src

.PHONY: install test run lint clean bundle db-up db-down migrate

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -e packages/correlis-schema -e packages/correlis-store -e 'services/api[dev]'

test:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q

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
