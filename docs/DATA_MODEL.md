# Canonical Data Model

## Design goals

The model must preserve facts, inference, time, and evidence without requiring a
specific vendor's event format.

It is OCSF-inspired at the observation boundary, but intentionally smaller and
focused on the attack-scene use case.

## Observation

An `Observation` is an immutable normalized security fact received from a
collector or created by an attributable analyst action.

Required properties include:

- Unique observation ID.
- Tenant ID.
- Event time and ingest time.
- Source and sensor identity.
- Event class and activity.
- Severity and confidence.
- Subject and optional object entities.
- Evidence reference.

An observation may directly state a relationship when the source telemetry
supports it. For example, a process-start event can directly state that a
process `RUNS_ON` an asset.

## Entity

An entity is a stable identity in the operational model. Initial entity types
include:

- Asset
- Application
- Identity
- Process
- Network endpoint
- Cloud resource
- Vulnerability
- IP address
- Domain
- File
- Certificate
- Data store

Source IDs are not automatically canonical IDs. Entity resolution is a separate,
auditable function.


## Core ontology contracts

The versioned core ontology defines identity candidates for each entity type. Examples include `hostname` and cloud instance attributes for assets, principal names for identities, process GUIDs for processes, sockets for network endpoints, CVEs for vulnerabilities, and content or host-path identifiers for files. These candidates are descriptive and machine-readable inputs for a future attributable entity-resolution projection; they do not automatically merge entities and are not required on every incoming `EntityRef`.

Relationship types have explicit directed source and target constraints. For example, assets can have vulnerabilities, domains resolve to IP addresses, processes run on assets, and the attack source points toward exploited or compromised targets. Reverse edges are not inferred automatically. Future connectors must validate against the ontology rather than treating relationships as universally connectable.

Operational-action contracts define the action actor, target, evidence, attributes, and optional or required reason. Every current action requires evidence and emits an observation. Sensitive actions such as confirming, rejecting, suppressing, requesting evidence, opening remediation, and recording containment decisions require a non-blank reason; owner assignment and evidence export may omit a reason. Recording an action is separate from applying any future state transition: actions become attributable analyst-action observations and do not by themselves mutate relationships, incidents, or entities.

## Evidence

An evidence reference points to immutable source material and includes a SHA-256
content hash. Evidence may be:

- A raw event.
- A configuration snapshot.
- A scanner finding.
- A threat-intelligence record.
- An analyst note.
- A derived artifact.

Evidence references are locators, not permission bypasses. The API must still
authorize access to the referenced material.

## Relationship

A relationship connects two entities and carries:

- Relationship type.
- Provenance.
- Confidence.
- First-seen and last-seen time.
- Evidence references.
- Optional rule/analytic ID.

## Provenance classes

### Observed

Directly represented by normalized source telemetry.

### Deterministic

Produced by a repeatable rule whose inputs and logic are inspectable.

### Analytic

Produced by a statistical or graph analytic method.

### AI-suggested

A model-generated hypothesis. It is not incident truth and must not silently
alter observed or confirmed state.

### Analyst-confirmed

Explicitly confirmed by an attributable analyst action.

### Analyst-rejected

Explicitly rejected by an attributable analyst action.

## Attack scene

An attack scene is a time-aware projection containing:

- Entities.
- Relationships.
- Ordered observations.
- Incident state.
- Analyst-action observations.
- Summary and uncertainty.

## Incident state

### Potential

The scene contains only possible exposure, privilege, or access paths.

### Observed

Activity associated with an attack or suspicious sequence has been observed.

### Confirmed

Deterministic evidence or an analyst action confirms compromise or impact.

### Contained

Containment actions have been recorded and the relevant propagation paths are no
longer active.

### Closed

The incident has a final disposition and required evidence retention metadata.

## Invariants

1. Every observation has at least one evidence reference.
2. Every relationship has at least one evidence reference.
3. A direct relationship requires both subject and object entities.
4. Derived relationships require a rule or analytic ID.
5. AI-suggested relationships cannot become confirmed without a separate
   analyst-confirmation observation or deterministic evidence.
6. Event time and ingest time are separate.
7. Projection logic is idempotent by observation ID.

## Persistence tables

`observations` stores normalized canonical `Observation` records. Each row is tenant-qualified by `(tenant_id, observation_id)`, stores query fields such as event time, source, event class, severity, and confidence, and keeps the complete canonical observation JSON plus a deterministic SHA-256 payload hash. Reusing an observation ID is idempotent only when the canonical payload hash is identical; a different payload for the same tenant-qualified ID is an immutable-record conflict, including under concurrent writes.

`evidence_refs` stores immutable `EvidenceRef` metadata, including source, locator, content SHA-256, collection time, complete canonical evidence-reference JSON, and a deterministic SHA-256 hash of that reference metadata. Identical tenant-qualified evidence references may be shared by multiple observations, but a different payload for the same evidence ID is an immutable-record conflict. The locator is not treated as integrity proof; evidence bytes are not stored in this table.

`observation_evidence` links observations to evidence references using tenant-qualified composite keys and foreign keys. Future immutable evidence-content storage is separate from these evidence-reference metadata rows.

## Collector authentication tables

### `collectors`

Tenant-scoped operational collector registry keyed by `(tenant_id, collector_id)`. Records include the collector name, normalized source, enabled or disabled status, metadata JSON, creation and update timestamps, disabled timestamp, and last successful authentication timestamp.

### `collector_credentials`

Credential metadata keyed by `credential_id` with a composite reference to the owning collector. Multiple credentials may overlap for rotation. The table stores token version, HMAC secret digest, creation time, optional expiration, optional revocation time, and last-used time. It never stores complete tokens or plaintext secrets.

### `collector_auth_events`

Append-only operational authentication audit records for successful and rejected collector authentication. Audit identifiers may be null for malformed or unknown credentials and events store request metadata only, never authorization headers, complete tokens, plaintext secrets, digests, or request bodies. These records are not canonical cyber observations in this PR.
