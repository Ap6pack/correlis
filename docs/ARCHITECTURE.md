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

`correlis-store` is the initial implementation of the durable normalized-observation and evidence-reference repository. It persists canonical `correlis-schema` observation and evidence-reference payloads with tenant-qualified identifiers, immutable payload hashes, and observation-to-evidence associations. The API, replay service, projections, incidents, and raw evidence-content storage remain outside this persistence foundation.
