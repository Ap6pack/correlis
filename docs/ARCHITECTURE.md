# Architecture

## Architectural intent

Correlis is designed as an event-sourced security model with rebuildable
projections. Raw observations are immutable. Entity, relationship, incident, and
visualization state are derived from those observations and can be reconstructed.

## Target architecture

```text
Collectors
  Outrider | Suricata | Sysmon | Entra | Cloud | Generic Webhook
      |
      v
Normalizer and validation boundary
      |
      v
Versioned ontology registry
      |
      v
Durable observation stream -----> Immutable evidence store
      |
      +-----> Entity projection
      +-----> Relationship projection
      +-----> Detection and sequence rules
      +-----> Incident/attack-scene projection
      |
      v
Query and subscription API
      |
      +-----> Attack Scene web application
      +-----> Case/export integrations
      +-----> Optional AI analyst service
```

## Core components

### Schema package

Defines the stable cross-service contracts:

- `Observation`
- `EntityRef`
- `EvidenceRef`
- `Relationship`
- `SceneDelta`
- `AttackScene`

Source-specific data belongs in validated attributes or source adapters, not in
new top-level fields added ad hoc.


### Ontology registry

The `correlis-ontology` package sits between normalization and projection. It is the shared semantic contract for collectors, deterministic rules, APIs, and UI views: which objects exist, how identity candidates are named, which relationships are valid, and which direction those relationships use. Views such as the graph, timeline, evidence inspector, and incident view are projections over this one model rather than separate data models.

Operational actions are also defined by the ontology. An action records an attributable actor, target, evidence, and any required reason; converting an action creates a new analyst-action observation rather than rewriting historical facts. Future semantic investigation can use structured ontology queries. Natural-language assistance may later translate requests into safe structured queries, but it cannot create underlying truth or bypass ontology validation and evidence requirements.

### Ingest service

Authenticates collectors, records source metadata, stores raw evidence, validates
normalized observations, and publishes them to the durable stream.

### Correlation service

Runs deterministic rules and temporal/graph correlation. Every derived
relationship must include:

- A provenance class.
- A confidence value.
- A rule or analytic identifier.
- One or more evidence references.
- First-seen and last-seen timestamps.

### Projection store

PostgreSQL is the initial system of record for entities, relationships,
incidents, configuration, and analyst actions. The graph is modeled explicitly
in relational tables first. A dedicated graph database requires measured query
or scale justification.

### Evidence store

Raw telemetry is stored separately from graph projections. Evidence records carry
content hashes so analysts can verify that a projection references the same
source material originally ingested.

### API and stream service

Provides authenticated queries, filtered live updates, replay controls, and
analyst actions. WebSocket messages use scene deltas rather than full-scene
replacement in production.

### Attack Scene application

The product UI synchronizes:

- Attack graph.
- Timeline and replay controls.
- Evidence inspector.
- Incident summary and uncertainty.

Geography is a contextual view, not the primary organizing model.

## Reliability requirements

The production stream must support:

- Consumer groups and explicit acknowledgement.
- Persistent checkpoints.
- At-least-once delivery with idempotent projection.
- Dead-letter handling.
- Replay from a durable event position.
- Consumer lag visibility.
- Tenant-aware partitioning.

The first reference API in this repository uses scenario files and in-memory
projection only. That implementation exists to prove contracts and replay
semantics; it must not be mistaken for the production data plane.

## Trust boundaries

1. Collector to ingest boundary.
2. Raw evidence to normalization boundary.
3. Normalized observation to deterministic projection boundary.
4. Projection/query API to browser boundary.
5. Evidence-backed model to optional AI interpretation boundary.

Each boundary requires authentication, authorization, validation, size limits,
rate limits, and auditability.

## Technology direction

- Python for ingestion, schemas, correlation, and API services.
- PostgreSQL for canonical state and projections.
- Durable streaming with replay; Redis Streams is acceptable for an initial durable deployment when configured
  with consumer groups and persistence.
- React and TypeScript for the investigation application.
- Object-compatible storage for raw evidence when filesystem storage no longer
  meets deployment requirements.

Kubernetes, Kafka, and a dedicated graph database are not baseline requirements.
They are scale decisions, not credibility signals.

## Durable observation store

`correlis-store` is the initial implementation of the durable normalized-observation and evidence-reference repository. It persists canonical `correlis-schema` observation and evidence-reference payloads with tenant-qualified identifiers, immutable payload hashes, and observation-to-evidence associations. PostgreSQL migrations and repository behavior are tested in CI in addition to fast SQLite unit coverage. Immutable writes include single-retry resolution for expected concurrent uniqueness races: after rollback, the repository compares canonical payload hashes to return an idempotent existing write, reuse identical evidence, or raise an immutable-record conflict. Unexpected database integrity failures remain visible after the bounded retry. The API, replay service, projections, incidents, and raw evidence-content storage remain outside this persistence foundation.

## API lifecycle and health

`correlis-api` is constructed through an application factory that accepts explicit settings, a scenario repository, and an optional externally managed database engine. The ASGI module entry point remains available for Uvicorn, but importing it only builds the FastAPI object and does not open a database connection.

Runtime resources are initialized through FastAPI lifespan management. The API stores settings, the file-backed scenario repository, any database engine, the SQLAlchemy session factory, and engine ownership metadata on application state. When the API creates an engine from configuration, it owns and disposes that engine during shutdown; injected engines remain externally managed.

Database construction remains owned by `correlis-store`, while `correlis-api` owns HTTP lifecycle and health semantics. Future operational routes should use the per-request database-session dependency, which opens one synchronous SQLAlchemy session per dependency invocation and closes it without implicit commits. Scenario replay remains file-backed and does not use PostgreSQL.

Liveness and readiness are separate. `/health` and `/health/live` only indicate that the API process is operating. `/health/ready` verifies database configuration, connectivity, and the current Alembic revision set against the repository heads. Readiness does not run migrations, create tables, or otherwise mutate schema; Alembic migrations remain an explicit deployment responsibility such as `make migrate`.

## Collector identity trust boundary

Collectors are operational service identities, separate from ontology entities, analyst identities, users, observations, incident actors, or authorization roles. Collector tokens use `correlis_v1.<credential_id>.<secret>`: the credential ID selects stored metadata while the secret is verified with HMAC-SHA-256 using a server-held `CORRELIS_CREDENTIAL_PEPPER`. The complete token and plaintext secret are returned only during issuance and are not persisted.

Successful authentication produces the trusted tenant context for future ingest routes: tenant ID and source come from the collector record, not request bodies or tenant override headers. Authentication appends operational audit events for both successful and rejected attempts. These audit events are not cyber observations. Collector and credential administration remains offline through `correlis-admin` until user authorization exists.

## Authenticated observation ingestion boundary

Authenticated ingestion accepts one canonical observation or a bounded batch only after collector authentication. The collector principal is authoritative for tenant and source scope: the submitted observation must already match the principal before the API creates a trusted copy with the principal tenant and source. Override headers, query parameters, unsigned claims, and request attributes are not trusted for scope.

Ingestion validation order is intentionally fail-closed: authenticate the collector, resolve the shared request ID, validate request schema, enforce tenant and source scope, validate direct relationships against the active ontology, and then persist through the immutable observation store. Immutable persistence keeps `ingest_time` as canonical normalized-observation data and uses the database `inserted_at` as platform storage time.

Single ingestion returns deterministic `created`, `existing`, or sanitized immutable-conflict responses. Batch ingestion prevalidates every item before any write, then commits each item independently in input order. Independent commits allow one immutable conflict to be reported while later valid items are still processed; complete batch retries are safe because previously committed identical observations return `existing`. Durable processing order, queues, projectors, and persistent Attack Scene construction remain future work and are not part of ingestion.

A request-correlation middleware resolves one safe request ID for HTTP requests and returns it in `X-Request-ID`. Collector authentication audit records, ingestion response bodies, and ingestion logs use that same ID while avoiding authorization headers, tokens, evidence locators, evidence metadata, entity attributes, and request bodies.

## Collector-scoped readback boundary

The collector readback API exposes immutable observation and evidence-reference metadata to authenticated collectors only within their own trust boundary. The authenticated collector principal is authoritative for tenant and source; clients cannot broaden scope with tenant headers, path parameters, query parameters, or unsigned state. Collector credentials therefore do not provide tenant-wide analyst access.

Operational read queries apply tenant and source predicates in SQL. Direct observation lookup filters by tenant ID, source, and observation ID in the database query, and list queries always filter by tenant ID and source before applying optional event-time, event-class, severity, sensor, cursor, or limit constraints. Evidence lookup is authorized through the observation-evidence association: an evidence reference is visible only when it is linked to at least one observation in the collector's tenant and source.

Observation browsing uses deterministic keyset pagination ordered by `event_time DESC, observation_id DESC`. The continuation cursor compares both event time and observation ID, avoiding offset pagination and preventing skipped or duplicated records when many observations share the same event timestamp. This is event-time browsing over the immutable observation view; durable processing-order sequences remain future work and require a separate cursor model.

## Durable observation stream boundary

Correlis assigns each committed observation a global ingest sequence as an internal processing boundary for future replay consumers, live-stream clients, and projector checkpoints. The sequence is allocated by atomically incrementing a singleton `observation_ingest_sequence_state` row inside the same transaction that persists the observation, evidence references, evidence associations, and `observation_ingest_entries` mapping.

A normal database sequence is insufficient for commit-safe polling because it can hand out values in one order while transactions commit in another. A consumer that observes a later committed value could advance past an earlier value that is still uncommitted. The singleton allocator row remains locked until the observation transaction commits or rolls back, so later allocations wait and cannot commit ahead of lower allocated observations.

The allocator high watermark is the committed `last_sequence` visible to a reader. Internal sequence reads capture that high watermark, scan `observation_ingest_entries` in ascending `ingest_sequence`, and do not read beyond the captured boundary. This processing order is intentionally separate from event-time browsing, which remains ordered by source event time for collector-facing observation queries.

## Projection runtime boundary

Correlis projectors are durable stream consumers over the global observation ingest sequence. Each projector is identified by a logical name and an independent version. The checkpoint is global because every projector scans the same central sequence; tenant- or event-specific projectors must inspect each sequence entry and intentionally no-op irrelevant observations rather than moving a filtered cursor past unexamined sequence positions.

A `ProjectionRunner` owns one bounded outer SQLAlchemy transaction per batch. At the start of that transaction it locks the projector checkpoint row with `FOR UPDATE NOWAIT`, so only one runner can execute a projector name/version at a time while different projector identities or versions can proceed independently. The runner captures the durable observation high watermark before loading items and processes only entries greater than the checkpoint and less than or equal to that captured value.

Each observation handler runs inside a per-item savepoint using the runner-owned session. On success, handler database effects, failure resolution, and checkpoint advancement remain in the same outer transaction. On handler failure, only the current savepoint is rolled back; successful earlier items in the same batch may still commit with their checkpoint advancement, while the failing sequence is recorded as an active poison-observation failure and later observations are not processed.

Normal runs are blocked while an active failure exists. Retry must be explicit and attempts the failed sequence; success requires the matching active failure record, resolves it, and retains the failure history. Missing, resolved, mismatched, or otherwise inconsistent failure state raises `ProjectionInvariantError` before handler execution rather than silently advancing. `ProjectionHandlerError` is the contract for an expected poison observation, while unexpected ordinary application exceptions are stored only as sanitized poison failures. SQLAlchemy and database exceptions are infrastructure failures: they roll back the batch, propagate to the caller, and do not create poison records or commit projection effects. Handlers may deliberately translate a deterministic database condition into `ProjectionHandlerError` when that condition is safe to classify as poison. Pause and resume acquire the same checkpoint `FOR UPDATE NOWAIT` row lock as runners, so lifecycle changes cannot overwrite active or newly failed runner state. Projector registration is safe under concurrent identical requests and does not reset existing checkpoints. There is no skip, manual resolve, rewind, or reset semantic in this runtime. Handlers must not perform external side effects, open independent projection transactions, commit, roll back, close the session, or mutate immutable observations. Future workers may schedule calls into this runtime, but no worker is started automatically yet.

## Durable collector observation stream

The durable observation stream uses server-sent events because observations flow one way from Correlis to a collector client and HTTP bearer authentication already exists. The route is collector-scoped: tenant, collector, and source come only from the authenticated collector principal, not from request headers, query parameters, or cursor plaintext.

Replay order is derived from the durable global `observation_ingest_entries` stream. The collector scanner first reads bounded global sequence metadata to determine tenant/source visibility, then loads full canonical observation payloads only for matching entries. This preserves authorization isolation while allowing cursor progress across invisible global entries.

Stream cursors are encrypted and authenticated with AES-GCM. The AES key is derived from `CORRELIS_CREDENTIAL_PEPPER` with HKDF/SHA-256 and stream-specific domain separation. Cursor payloads bind the internal position to tenant ID, collector ID, and source, but not to credential ID, so ordinary credential rotation does not invalidate cursors. Clients see only opaque `ocs1.*` tokens.

The cursor position is the last durable global position scanned, not the last visible observation or event time. When a scan advances through entries outside the collector scope, the API emits a `checkpoint` control event so clients can resume without repeatedly scanning irrelevant entries. Reconnect semantics are deterministic and at least once.

Database sessions are short-lived for stream authentication, initial high-watermark preflight, each bounded scanner poll, and periodic credential/collector revalidation. The stream does not hold a request-scoped SQLAlchemy session while waiting for clients or sleeping. Response iteration provides direct backpressure: the next page is not read until the current page's events have been yielded.

Connection limits are maintained in process with async-lock-protected counters for a global limit and per-tenant/per-collector limits. This PR intentionally does not add Redis, Kafka, a broker, a background publisher, or distributed stream coordination. Polling is PostgreSQL-first and relies on the existing durable sequence tables.

### Durable observation stream lifecycle

`GET /api/v1/streams/observations` validates static request state before acquiring capacity. Empty or malformed cursors, invalid `Last-Event-ID` values, cursor conflicts, cursor/start conflicts, and forbidden tenant or source overrides fail without consuming a stream lease. After a lease is acquired, high-watermark preflight uses a short-lived database session and releases capacity on any preflight or cursor-ahead failure.

Stream leases are released from both the generator `finally` block and the response background finalizer. The release operation is idempotent, so this double protection safely handles normal completion, cancellation, disconnects, stream failures, and response-lifecycle cleanup when a generator is not fully consumed.

Authorization revalidation is performed when the configured deadline elapses before scans, observations, checkpoints, and heartbeat comments. The security boundary is per emitted event: a collector whose credentials or collector record are revoked, expired, disabled, removed, or source-reassigned can finish the event already in flight, but receives no following observation or checkpoint once revalidation detects inactivity. Prefetched pages are bounded and do not bypass this check.

The stream keeps direct response backpressure. It does not use queues, background producer tasks, fan-out caches, brokers, or long-lived database sessions. A scan returns one bounded page, the response iterator yields directly from that page, and the next scan occurs only after the page has been consumed. Reconnect delivery remains at least once, with encrypted collector-scoped `ocs1.*` cursors and no raw global sequence exposure in event payloads.

## Persistent entity projector boundary

The entity projector is a concrete bounded projection over immutable observations. Output tables are version-qualified so `entity-projection/1` and `entity-projection/2` can coexist and rebuild independently. Entity identity is the projection version, tenant ID, and submitted entity ID. The canonical entity key is a deterministic SHA-256 digest of compact sorted JSON containing only the entity type and submitted ID.

Within one projected identity, entity type is immutable. Current label and attributes are selected by latest observation event time, with durable ingest sequence as the tie-breaker. Attributes are replaced as a complete submitted snapshot and are not merged field by field. First and last seen use source event time, while ingest sequence boundaries preserve processing lineage.

The projection records observation lineage, evidence lineage, and exact ontology identity-key claims. Identity claims are evidence for future attributable entity-resolution work; they do not perform automatic entity resolution, fuzzy matching, aliasing, or merging. Entity output and checkpoint advancement are committed atomically by the projection runtime. Operators execute bounded CLI runs explicitly today; future worker execution can use the same projector identity and handler boundary.

## Persistent direct relationship projection

The relationship projection is a separate versioned projector named `relationship-projection`. Operators explicitly register and run each version; no automatic registration, background worker, scheduler, queue, or HTTP relationship API is added. The projector reads Correlis's durable global observation ingest sequence with the existing transactional `ProjectionRunner`, so relationship writes and checkpoint advancement commit atomically and deterministic poison observations are recorded with sanitized failure messages.

The projector is operationally independent from the entity projection. It validates the subject, object, and directed relationship against the configured ontology and persists endpoint IDs and endpoint entity types directly from the canonical observation. It intentionally has no foreign key to `entities`; later graph-building can choose compatible entity and relationship projection versions without requiring relationship materialization to wait for entity projection catch-up.

Explicit observed relationships are materialized when `observation.relationship is not None` and `observation.object is not None`. The operator-controlled correlation projector also materializes deterministic relationships for `COR-SEQ-001`. Observed rows have no rule identity; deterministic rows require durable nonblank `rule_id` and `rule_version`. The shared 32-character relationship ID remains unchanged, and deterministic uniqueness includes the rule ID so separate deterministic rules may produce the same directed edge. The persistent projectors do not implement analytic or AI-generated relationships, analyst decisions, incidents, Attack Scene persistence, entity merging, or automatic entity resolution.

## Correlation projector configuration

The `correlation-projection` identity runs deterministic correlation under operator control. Registration is specialized: operators use `correlis-admin correlation-projection register` instead of the generic projector registration path so an initial checkpoint and configuration row are created in one atomic transaction.

Each correlation configuration references exactly one existing `relationship-projection` version and one immutable correlation ruleset name/version. That stored graph version is authoritative at run time. Before evaluating trigger sequence `N`, correlation requires the configured relationship projection checkpoint to have `last_processed_sequence >= N`. The relationship projector may be paused or failed and still satisfy already-committed sequences, but correlation never runs, waits for, or repairs the dependency automatically.

Operators execute one bounded batch at a time:

```bash
correlis-admin relationship-projection run \
  --version 1 \
  --limit 100

correlis-admin correlation-projection run \
  --version 1 \
  --limit 100
```

Correlation rulesets are resolved by exact immutable name and version. The built-in `correlis-sequence/1` immutable ruleset manifest is stored with the configuration and currently contains only `COR-SEQ-001`, `Exploit against known vulnerability`; `correlis-sequence/2` contains `COR-SEQ-001` and `COR-SEQ-002`. Stored configurations never automatically upgrade, and new rules require a new ruleset version. Operators can register a new correlation projection version and relationship graph version for a future new ruleset. `COR-SEQ-001` executes as a pure operation over the stored relationship and observation-lineage tables. Historical support is bounded by durable ingest sequence, which ensures prior observed `has_vulnerability` support is strictly before the trigger observation and avoids event-time ordering or aggregate latest-state evidence; future relationship state cannot affect an earlier trigger. Matching exploit attempts persist deterministic `exploited` relationships with rule ID `COR-SEQ-001`, rule version `1`, deterministic provenance, and confidence `0.85`. `correlis-sequence/2` registers `COR-SEQ-001` and `COR-SEQ-002` and executes them in immutable manifest evaluation order. Version 2 produces the durable chain from prior observed `has_vulnerability`, to deterministic `exploited`, to deterministic `compromised` for later suspicious process activity after a prior exploit against the same target. `COR-SEQ-002` is sequence-bounded and uses prior observed or deterministic `exploited` relationship lineage rather than event time or aggregate latest state. New rulesets require separate correlation projection versions and separate relationship projection versions; stored configurations never automatically upgrade. `COR-SEQ-003` now exists only as a pure staged evaluator and is not part of any active ruleset yet.

Correlation output, trigger observation lineage, aggregate relationship evidence, derivation records, support relationship lineage, evidence-role lineage, and checkpoint advancement commit atomically through `ProjectionRunner`. The system has no background correlation worker, scheduler, queue, public correlation API, dynamic rule loading, AI rule generation, incident persistence, or Attack Scene persistence.

## Correlation derivation lineage storage

The store now has durable correlation derivation lineage tables for deterministic relationships produced by a future correlation projector. The schema isolates lineage by tenant, relationship projection version, relationship ID, and trigger observation identity. Supporting relationships are referenced by relationship ID only and must exist in the same tenant and relationship projection version. Evidence lineage stores only evidence IDs and the role that evidence played (`trigger` or `support`); it does not expose locators, raw observation payloads, metadata, or evidence bytes through relationship lineage reads.

The correlation projector writes this lineage for configured deterministic rules atomically with deterministic relationship output and checkpoint advancement.

## COR-SEQ-003 pure evaluator staging

The correlation package includes a staged pure evaluator for `COR-SEQ-003`, which correlates authentication from a previously compromised source entity to another entity and derives a `moved_laterally_to` candidate. Its historical support lookup is bounded by durable ingest sequence lineage: only observed or deterministic `compromised` relationships with relationship-observation sequences strictly earlier than the authentication trigger can support the candidate. Future relationship evidence is excluded from historical evaluation.

The evaluator is intentionally side-effect free. It returns an immutable candidate or `None`, performs no database writes, creates no projector failure records, and is not registered in durable dispatch yet. Active ruleset version 2 remains unchanged and continues to execute only `COR-SEQ-001` and `COR-SEQ-002`; ruleset version 3 and durable `COR-SEQ-003` execution will be added in the next PR.
