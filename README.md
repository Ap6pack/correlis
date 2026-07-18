# Correlis

> **See the attack as a system.**

Correlis is an open, self-hosted cyber operations platform that turns exposure,
identity, network, endpoint, cloud, and threat data into an evidence-backed live
attack model.

Correlis is not a SIEM replacement, a threat-map screensaver, or an LLM that
invents incidents. It is the operational layer that reconstructs how security
facts relate, how an attack changes over time, and why an analyst should trust
each conclusion.

## Product state

**Pre-alpha foundation.** The repository currently contains:

- Versioned cyber observation, entity, relationship, evidence, and attack-scene contracts.
- Explicit provenance classes: observed, deterministic, analytic, AI-suggested,
  analyst-confirmed, and analyst-rejected.
- A deterministic scene builder with evidence-linked derived relationships.
- A FastAPI service for scenario inspection and WebSocket replay.
- A complete synthetic initial-access-to-lateral-movement reference scenario.
- Public architecture and canonical data-model documentation.

The current scenario service is a contract and replay vertical slice. It is not
the production event store.

## What makes Correlis different

Correlis maintains three states in one operational model:

1. **Possible** — exposure and access paths that could be used.
2. **Observed** — behaviors directly seen in telemetry.
3. **Confirmed** — conclusions backed by evidence or analyst confirmation.

Every relationship carries its source, timestamps, confidence, derivation
method, and evidence references. AI-generated interpretation is visually and
structurally separate from observed facts.

## Quick start

Requirements: Python 3.11+

```bash
make install
make test
make run
```

Then open:

- API documentation: `http://localhost:8080/docs`
- Scenario list: `http://localhost:8080/api/v1/scenarios`
- Built attack scene: `http://localhost:8080/api/v1/scenarios/initial-access-demo/scene`
- WebSocket replay: `ws://localhost:8080/ws/scenarios/initial-access-demo/replay?speed=10`

## Repository layout

```text
packages/correlis-schema/   Stable cyber contracts
services/api/               Reference API and replay service
scenarios/                  Reproducible attack scenarios
docs/                       Public architecture and data-model documentation
```

## Development principles

- Evidence before explanation.
- Deterministic correlation before probabilistic interpretation.
- Replayability is a core feature, not a debugging afterthought.
- Raw evidence is immutable; projections can be rebuilt.
- Collectors are replaceable and cannot define the canonical model.
- No external AI dependency for detection, correlation, or replay.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Persistence development

The durable observation and evidence-reference store is developed against PostgreSQL. The
`correlis` database is the normal local development database.

```bash
cp .env.example .env
make db-up
make migrate
```

Optional PostgreSQL integration tests use a separate disposable `correlis_test`
database. With the Docker PostgreSQL service running, create it once with:

```bash
docker compose exec postgres createdb -U correlis correlis_test || true
make test-postgres
```

PostgreSQL integration tests may migrate, downgrade, and truncate the test tables
in `correlis_test`. The normal `make test` command remains SQLite-backed and does
not require PostgreSQL.

This configures only the local persistence foundation; the current API does not
ingest observations into, or query observations from, this database.
