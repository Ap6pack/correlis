# Local development

Correlis currently provides a FastAPI application, Swagger/OpenAPI documentation,
JSON scenario and operational endpoints, and a demonstration WebSocket replay.
It does **not** yet provide the final Attack Scene browser interface. The browser
view available today is the API documentation at `http://localhost:8080/docs`.

## Requirements

- Python 3.11 or newer. Python 3.13 is supported and tested in CI.
- Docker Engine.
- Docker Compose v2 (`docker compose`).
- GNU Make.

Check the local tools:

```bash
python3 --version
docker --version
docker compose version
```

## First-time setup

From the repository root:

```bash
make clean
make local-setup
make local-db
```

`make local-setup` performs the following actions:

- Creates `.venv` using the local `python3` interpreter.
- Installs the schema, ontology, store, API, and development dependencies.
- Creates `.env` from `.env.example` when `.env` does not exist.
- Generates a private local collector-credential pepper when the setting is blank.
- Preserves an existing `.env` and existing pepper.

The pepper is written only to the ignored `.env` file and is not printed.

`make local-db` performs the following actions:

- Starts PostgreSQL 16 through Docker Compose.
- Waits for PostgreSQL readiness.
- Creates the disposable `correlis_test` integration-test database when needed.
- Migrates the normal local `correlis` database to the current Alembic head.

Validate the setup:

```bash
make doctor
```

## Run the API

Start Correlis in the first terminal:

```bash
make run
```

Uvicorn runs with reload enabled at `http://localhost:8080`.

Open these browser locations:

- Swagger API documentation: `http://localhost:8080/docs`
- ReDoc documentation: `http://localhost:8080/redoc`
- Liveness: `http://localhost:8080/health/live`
- Readiness: `http://localhost:8080/health/ready`
- Core ontology: `http://localhost:8080/api/v1/ontology`
- Scenario list: `http://localhost:8080/api/v1/scenarios`
- Built demonstration scene: `http://localhost:8080/api/v1/scenarios/initial-access-demo/scene`

The demonstration replay WebSocket is:

```text
ws://localhost:8080/ws/scenarios/initial-access-demo/replay?speed=10
```

## Smoke-test the running API

With `make run` still active, use a second terminal:

```bash
make smoke
```

The smoke test checks:

- API liveness.
- Database and migration readiness.
- Ontology output.
- Scenario discovery.
- Attack-scene construction.

To test another host or port:

```bash
CORRELIS_BASE_URL=http://127.0.0.1:8080 make smoke
```

## Run the automated tests

SQLite-backed unit and API tests:

```bash
make test
```

PostgreSQL integration tests:

```bash
make test-postgres
```

All tests and linting:

```bash
make test-all
make lint
```

The PostgreSQL integration suite may migrate, downgrade, and truncate the
`correlis_test` database. It does not use the normal local `correlis` database.

## Test collector authentication

Load `.env` into the current shell because direct CLI commands do not run through
Make:

```bash
set -a
source .env
set +a
```

Create a local collector:

```bash
.venv/bin/correlis-admin collectors create \
  --tenant-id tenant-a \
  --collector-id collector-1 \
  --name "Local Outrider" \
  --source outrider
```

Issue a credential:

```bash
.venv/bin/correlis-admin credentials issue \
  --tenant-id tenant-a \
  --collector-id collector-1 \
  --name local
```

The complete bearer token is displayed once. Copy it into the shell without
committing it anywhere:

```bash
export CORRELIS_COLLECTOR_TOKEN='correlis_v1...'
```

Verify authentication:

```bash
curl \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  http://localhost:8080/api/v1/collectors/me
```

## Stop or reset local services

Stop PostgreSQL while preserving its Docker volume:

```bash
make db-down
```

Delete the local PostgreSQL volume and all local Correlis database data:

```bash
docker compose down -v
```

Rebuild the Python environment after dependency or interpreter changes:

```bash
make clean
make local-setup
```

## Troubleshooting

### `make test` passes but warnings appear

Rebuild the virtual environment after pulling dependency changes:

```bash
make clean
make local-setup
make test
```

### Readiness returns HTTP 503

Run:

```bash
make local-db
```

Then confirm that `.env` contains nonblank values for:

```text
CORRELIS_DATABASE_URL
CORRELIS_CREDENTIAL_PEPPER
```

The liveness and scenario endpoints can operate without full database readiness,
but ingestion, collector authentication, queries, and streaming require the
configured and migrated database.

### Port 5432 is already in use

Stop the other PostgreSQL service or change the host-side Compose port and both
database URLs in `.env`.

### Port 8080 is already in use

Stop the other service or run Uvicorn directly on another port:

```bash
PYTHONPATH=packages/correlis-schema/src:packages/correlis-ontology/src:packages/correlis-store/src:services/api/src \
.venv/bin/python -m uvicorn correlis_api.app:app --host 0.0.0.0 --port 8081 --reload
```

Then run:

```bash
CORRELIS_BASE_URL=http://localhost:8081 make smoke
```
