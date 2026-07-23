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
packages/correlis-ontology/ Versioned object, relationship, and action semantics
services/api/               Reference API and replay service
scenarios/                  Reproducible attack scenarios
docs/                       Public architecture and data-model documentation
```

## Ontology-driven operational model

Correlis uses one shared operational model for collectors, deterministic rules, APIs, and future UI views. The versioned core ontology documents entity types, identity candidates, valid directed relationship source and target types, and evidence-backed operational actions. Identity candidates describe future entity-resolution inputs; they do not automatically merge entities or replace explicit entity IDs.

Operational actions are attributable to an actor and target, require evidence, and require reasons for sensitive decisions. Actions become new `analyst_action` observations so decisions are auditable facts rather than rewrites of historical records. The machine-readable contract is available from `GET /api/v1/ontology`.

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

## API health checks

The API exposes three health-check endpoints:

- `GET /health` remains available for compatibility and reports API liveness.
- `GET /health/live` reports that the API process is operating; it does not access PostgreSQL or the filesystem.
- `GET /health/ready` reports whether the API is ready for database-backed operational endpoints by checking that PostgreSQL is configured, reachable, and at the expected Alembic migration head.

Readiness checks never create tables or run migrations. Operators must run `make migrate` as a separate deployment step before expecting readiness to pass. Scenario endpoints remain usable as reference functionality, but later operational endpoints will require readiness.

## Collector authentication

Collectors authenticate with opaque bearer credentials formatted as `correlis_v1.<credential_id>.<secret>`. Generate a private server-held pepper before issuing credentials:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Set that value as `CORRELIS_CREDENTIAL_PEPPER`, run database migrations, and use the offline `correlis-admin` command to manage collectors and credentials:

```bash
make migrate
correlis-admin collectors create --tenant-id tenant-a --collector-id collector-1 --name "Outrider Production" --source outrider
correlis-admin credentials issue --tenant-id tenant-a --collector-id collector-1 --name primary
curl -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" http://localhost:8080/api/v1/collectors/me
correlis-admin credentials revoke --credential-id <credential-id>
correlis-admin collectors disable --tenant-id tenant-a --collector-id collector-1
```

The credential issuance command displays the complete token exactly once; complete tokens and plaintext secrets are never stored. Credentials are tenant-scoped through their collector, multiple active credentials support rotation, and disabling a collector invalidates all associated credentials. Administration is currently offline through `correlis-admin`; there is no user login or public administration API yet.

## Authenticated observation ingestion

Collectors can submit canonical observations after authenticating with a bearer collector token. The authenticated collector principal is the trusted source for `tenant_id`, `source`, `collector_id`, and `credential_id`; observation body values for tenant and source must match that principal and cannot be overridden by headers, query parameters, or route parameters.

### Single ingestion

```bash
curl \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-ingest-001" \
  --data @observation.json \
  http://localhost:8080/api/v1/observations
```

A new immutable observation returns `created` with HTTP 201. An identical complete-request retry returns HTTP 200 with `existing`. A changed immutable observation or evidence payload for an existing identifier returns HTTP 409 without exposing stored payloads, hashes, tokens, digests, or pepper material.

### Batch ingestion

```bash
curl \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  -H "Content-Type: application/json" \
  --data @observation-batch.json \
  http://localhost:8080/api/v1/observations/batch
```

Batches use `{ "observations": [...] }`, require at least one item, and are bounded by `CORRELIS_INGEST_MAX_BATCH_SIZE`. Batch prevalidation checks every tenant scope, source scope, and ontology relationship before writing any item. After prevalidation succeeds, items are persisted in input order with independent commits; per-item conflicts are reported without stopping later valid items, and retrying the complete batch is safe because identical records return `existing`. Duplicate IDs are processed in input order, so identical duplicates produce `created` then `existing`, while conflicting duplicates produce `created` then `conflict`.

Request IDs are resolved once per HTTP request from a safe `X-Request-ID` value or a generated UUID. The same ID appears in authentication audit events, ingestion response bodies, response headers, and operational logs. Ingestion requires `application/json` or `application/*+json`; `CORRELIS_INGEST_MAX_BODY_BYTES` limits request bytes independently of the item-count limit. Ingestion validates the canonical schema and core ontology, but it does not yet build a persistent Attack Scene or publish observations to a durable processing stream.

## Collector observation readback

Authenticated collectors can read back immutable observations through a collector-scoped API. Collector credentials are not analyst accounts: each query is restricted to the tenant and normalized source bound to the authenticated collector credential, and a collector cannot query another source in the same tenant.

Direct observation lookup:

```bash
curl \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  -H "X-Request-ID: query-001" \
  http://localhost:8080/api/v1/observations/obs-123
```

List observations:

```bash
curl \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  "http://localhost:8080/api/v1/observations?event_class=exposure_finding&limit=50"
```

Continue pagination:

```bash
curl \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  "http://localhost:8080/api/v1/observations?event_class=exposure_finding&limit=50&cursor=<cursor>"
```

Evidence-reference metadata lookup:

```bash
curl \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  http://localhost:8080/api/v1/evidence/evidence-123
```

Observation listing supports filters for event-time bounds, event class, severity, sensor ID, and an optional source parameter that must match the authenticated collector source. Pagination uses stable keyset ordering by `(event_time, observation_id)` rather than offsets, so observations sharing an event timestamp are not skipped or duplicated. Cursors are opaque continuation state and must be reused with the same filters.

Evidence is visible only through an associated observation that is visible to the collector. The evidence endpoint returns canonical evidence-reference metadata, not raw evidence bytes, and it does not fetch evidence locators. Tenant-wide analyst queries and durable processing-order cursors are future work requiring user authorization and will not use this event-time cursor.

## Durable processing order

Every committed observation receives an internal global ingest sequence. This sequence is a safe processing order for Correlis-managed consumers, not source event time. Sequence assignment is transactional with observation and evidence persistence, and Correlis serializes allocation through a singleton database row so allocation order cannot race ahead of commit order. Rolled-back writes do not become visible and do not leave durable cursor gaps. Collector APIs do not expose the global sequence, and existing observation list pagination remains event-time based. Future projectors will consume this durable sequence as their processing boundary.

## Projection checkpoints

Projectors consume Correlis's durable global observation sequence as versioned stream consumers. A projector identity is the composite of `projector_name` and `projector_version`, so a new version starts from checkpoint sequence `0` and can coexist with earlier versions.

Projection handlers are database-only: projection writes and checkpoint advancement commit in the same SQLAlchemy transaction. Each projector version is protected by an exclusive checkpoint row lock while a bounded batch runs. The runner captures the observation high watermark at batch start, processes sequence entries in ingest-sequence order, and does not include observations that arrive after that boundary until a later run.

When a handler raises `ProjectionHandlerError`, Correlis treats it as an expected poison observation: the item savepoint rolls back, a durable sanitized projector failure is recorded, the checkpoint remains immediately before the failed observation, and normal runs are blocked so the failed observation cannot be skipped. Unexpected ordinary application exceptions are also recorded as sanitized poison failures without storing raw exception messages. SQLAlchemy and database exceptions are infrastructure failures instead: the batch transaction rolls back, the exception propagates, no poison record is created, and handler effects and checkpoint changes are not committed. A handler that wants to classify a deterministic database condition as poison must catch the SQLAlchemy exception itself and raise `ProjectionHandlerError` with a safe code and message. Retry is explicit; failed checkpoints must match an active failure record, and inconsistent missing, resolved, tenant, observation, or sequence state halts processing with an invariant error rather than silently advancing. Pause and resume serialize through the same checkpoint row lock as runners, and concurrent registration of the same projector identity is idempotent. This PR adds the checkpoint and failure runtime only; no background worker exists yet.

Inspect and control projector operational state with `correlis-admin`:

```bash
correlis-admin projectors register \
  --name entity-projection \
  --version 1
```

```bash
correlis-admin projectors list
```

```bash
correlis-admin projectors pause \
  --name entity-projection \
  --version 1
```

```bash
correlis-admin projection-failures list \
  --name entity-projection \
  --version 1 \
  --status active
```

## Durable observation stream

Correlis exposes a collector-scoped live observation stream at `GET /api/v1/streams/observations` using server-sent events (`text/event-stream`). The endpoint uses the same bearer collector authentication as ingestion, and the authenticated collector principal supplies the tenant, collector ID, and source scope. Stream clients receive only observations for their own tenant and source; there is no analyst-wide or tenant-wide stream in this milestone.

By default, a connection starts at the latest committed durable observation position, so existing history is not replayed. Use `start=earliest` to replay retained observations for the collector scope and then continue tailing new observations. Reconnects can resume with either the encrypted cursor returned in event IDs/data or the standard SSE `Last-Event-ID` header. Cursor values are client-held encrypted continuation state, not raw database sequence numbers, and the raw global ingest sequence and high watermark are never exposed.

Events are emitted in deterministic durable sequence order. `observation` events contain the canonical Observation payload, while `checkpoint` events advance the cursor across global entries outside the collector's tenant/source scope. Heartbeat comments (`: keepalive`) keep idle connections active. Delivery is at least once around reconnect boundaries, so clients should tolerate duplicate observations after reconnecting from an older valid cursor.

The stream uses bounded database scans and writes events directly to the HTTP response without a background broker, queue, Redis, Kafka, or fan-out publisher. Connection limits are enforced per API process globally and per collector. Long-lived streams periodically recheck credential and collector status, closing the stream if a credential is revoked or expired, a collector is disabled, or its source changes.

Native browser `EventSource` cannot set an `Authorization` header. Browser clients should currently use authenticated `fetch()` streaming or another HTTP client until user-session authentication exists.

### Curl latest

```bash
curl -N \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  -H "Accept: text/event-stream" \
  http://localhost:8080/api/v1/streams/observations
```

### Replay earliest

```bash
curl -N \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  -H "Accept: text/event-stream" \
  "http://localhost:8080/api/v1/streams/observations?start=earliest"
```

### Resume

```bash
curl -N \
  -H "Authorization: Bearer $CORRELIS_COLLECTOR_TOKEN" \
  -H "Accept: text/event-stream" \
  -H "Last-Event-ID: $CORRELIS_STREAM_CURSOR" \
  http://localhost:8080/api/v1/streams/observations
```

### Durable observation stream hardening

The collector observation stream performs static request validation before acquiring stream capacity. Cursor text, `Last-Event-ID`, conflicting cursor sources, `start` conflicts, and tenant/source override attempts are rejected before a lease is acquired. Once capacity is acquired, preflight uses a short-lived database session to read the committed observation high watermark directly, and any preflight failure releases the lease.

Capacity cleanup is deliberately idempotent and occurs through two lifecycle paths: the stream generator releases the lease in `finally`, and the `StreamingResponse` has a background finalizer that releases the same lease after response cleanup. This covers completion, cancellation, disconnects, and response paths where iteration does not fully consume the generator.

Authorization revalidation is deadline-based and occurs before scans, before each observation, before checkpoints, and before heartbeat comments when the recheck interval has elapsed. A revoked, expired, disabled, removed, or source-reassigned collector may finish an event already being transmitted, but after revalidation detects inactivity it receives no subsequent observation or checkpoint. Bounded prefetched pages do not override this boundary.

The stream preserves direct backpressure: there is no queue, producer task, broker, fan-out cache, or background poller. The next bounded page is not scanned until the response iterator resumes through every event from the current page. Database sessions remain short-lived and are not held while waiting on a client.

Delivery remains at least once around reconnect boundaries. Stream cursors remain encrypted `ocs1.*` tokens scoped to the collector tenant, collector ID, and source, and raw global sequence values are not exposed in collector-facing payloads.

## Persistent entity projection

Correlis includes a persistent entity projection that consumes the durable observation sequence through the existing transactional `ProjectionRunner`. Entity identity is tenant-qualified and uses the submitted entity ID within an explicit projector version. Entity type is immutable for that identity, and a conflicting type stops the projector with a durable failure instead of merging or rewriting records.

The projection stores a deterministic canonical entity key derived from entity type and submitted entity ID. First-seen and last-seen values use the source observation event time. Current label and attributes use the latest event time, with ingest sequence as a deterministic tie-breaker. Attribute snapshots are replaced completely rather than merged field by field so stale keys do not linger.

Observation lineage and evidence lineage are retained for projected entities. Ontology identity keys produce exact identity-key claims for future entity-resolution work, but those claims do not automatically merge entities. Different entity-projector versions can coexist side by side and rebuild independently. No worker runs automatically, and no public entity API exists yet; operators run bounded local administrative commands explicitly.

```bash
correlis-admin entity-projection register --version 1
```

```bash
correlis-admin entity-projection run --version 1 --limit 100
```

```bash
correlis-admin entities list \
  --projection-version 1 \
  --tenant-id tenant-a
```

```bash
correlis-admin entities lineage \
  --projection-version 1 \
  --tenant-id tenant-a \
  --entity-id asset-123
```

### Persistent relationship projection

Correlis includes an operator-run, durable `relationship-projection` projector for explicit relationships already present in canonical observations. It materializes only observations where `relationship` and `object` are both present; all persisted edges are `observed`, direct-evidence relationships with no rule ID. The projector consumes the same global observation ingest sequence as other projectors through `ProjectionRunner`, is scoped by tenant and projector version, and stores source/target entity IDs and types without requiring the entity projector to be registered or caught up first.

Relationship IDs are deterministic SHA-256-derived 32-character IDs over tenant, source ID, relationship type, target ID, provenance, and rule ID (`direct` for direct observations). The shared 32-character relationship-ID algorithm is unchanged. Persistent relationship storage now accepts only `observed` and `deterministic` provenance: observed rows must not have rule identity, while deterministic rows require nonblank `rule_id` and `rule_version`. Observed edges remain unique by directed endpoints and type; deterministic edges are unique by directed endpoints, type, and rule ID, so separate deterministic rules may produce the same directed edge. Aggregation is order-independent: first/last seen and first/last ingest sequence use min/max bounds, while confidence keeps the maximum observed confidence. Observation lineage and aggregate evidence lineage are retained for inspection. No entity merging, background worker, or public relationship HTTP API is introduced.

CLI examples:

```bash
correlis-admin relationship-projection register --version 1
correlis-admin relationship-projection show --version 1
correlis-admin relationship-projection run --version 1 --limit 100
correlis-admin relationship-projection run --version 1 --limit 100 --retry-failed
correlis-admin relationships list --projection-version 1 --tenant-id tenant-a
correlis-admin relationships list --projection-version 1 --tenant-id tenant-a --provenance observed
correlis-admin relationships list --projection-version 1 --tenant-id tenant-a --provenance deterministic --rule-id <rule-id>
correlis-admin relationships show --projection-version 1 --tenant-id tenant-a --relationship-id <id>
correlis-admin relationships lineage --projection-version 1 --tenant-id tenant-a --relationship-id <id>
```

### Correlation projector configuration

Correlis includes an operator-controlled `correlation-projection` projector that executes the built-in pure `COR-SEQ-001` evaluator through `ProjectionRunner`. Correlation projection must be registered with the specialized CLI so the checkpoint and durable configuration are created atomically:

```bash
correlis-admin correlation-projection register \
  --version 1 \
  --relationship-projection-version 1 \
  --ruleset-name correlis-sequence \
  --ruleset-version 1
correlis-admin correlation-projection show --version 1
correlis-admin correlation-projection rules --version 1
```

Operators run the configured relationship projection first, then run one bounded correlation batch:

```bash
correlis-admin relationship-projection run \
  --version 1 \
  --limit 100

correlis-admin correlation-projection run \
  --version 1 \
  --limit 100
```

Correlation rulesets are resolved by immutable ruleset name and version. The existing `correlis-sequence/1` ruleset contains only `COR-SEQ-001`, and the new `correlis-sequence/2` ruleset contains `COR-SEQ-001` followed by `COR-SEQ-002`. The stored correlation configuration is authoritative: the rules and run commands load the configured relationship graph version, resolve the exact stored ruleset identity, and verify the stored ruleset manifest and hash before executing. Stored configurations never automatically upgrade; new rules require a new ruleset version, and operators can register a new correlation projection version with a new relationship graph version for that ruleset. The relationship projection checkpoint must already be caught up through each trigger ingest sequence; correlation does not run, wait for, or repair the relationship projector automatically.

`correlis-sequence/1` executes only `COR-SEQ-001` (`Exploit against known vulnerability`) through the durable correlation projector. It derives deterministic `exploited` relationships from exploit attempts against entities with prior observed `has_vulnerability` support in the configured relationship graph. Historical support is bounded by durable ingest sequence, not event time or aggregate latest relationship state, so future relationship state cannot affect an earlier trigger. `correlis-sequence/2` executes `COR-SEQ-001` and then `COR-SEQ-002` in immutable manifest evaluation order. Version 2 produces the durable chain from prior observed `has_vulnerability`, to deterministic `exploited`, to deterministic `compromised` for later suspicious process activity after a prior exploit against the same target. `COR-SEQ-002` is sequence-bounded and reads prior observed or deterministic `exploited` relationship lineage. New rulesets require separate correlation projection versions and separate relationship projection versions; stored configurations never automatically upgrade. `COR-SEQ-003` is not implemented yet. The projector persists deterministic relationship output, trigger observation lineage, aggregate evidence, derivation records, supporting relationship IDs, and trigger/support evidence roles atomically with correlation checkpoint advancement for every candidate returned by the configured immutable ruleset. Correlis still has no background correlation worker, scheduler, queue, public correlation API, dynamic rule loading, AI rule generation, incident persistence, or Attack Scene persistence.

### Correlation derivation lineage storage

Migration `0009_correlation_lineage` adds durable read-side lineage tables for future deterministic relationship derivations. `relationship_derivations` records the tenant, relationship projection version, derived relationship ID, trigger observation ID, trigger ingest sequence, correlation projection version, rule identity, confidence, reason code, and timestamps. `relationship_derivation_supports` stores only supporting relationship identities in the same tenant and relationship projection version, and `relationship_derivation_evidence` stores evidence IDs with `trigger` or `support` roles only.

The correlation projection handler writes this lineage for `COR-SEQ-001` in the same transaction as deterministic relationship output and checkpoint advancement.
