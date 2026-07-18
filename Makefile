PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
PYTHONPATH := packages/correlis-schema/src:services/api/src

.PHONY: install test run lint clean bundle

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -e packages/correlis-schema -e 'services/api[dev]'

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
