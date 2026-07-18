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
- Analyst actions.
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

`observations` stores normalized canonical `Observation` records. Each row is tenant-qualified by `(tenant_id, observation_id)`, stores query fields such as event time, source, event class, severity, and confidence, and keeps the complete canonical observation JSON plus a deterministic SHA-256 payload hash.

`evidence_refs` stores immutable `EvidenceRef` metadata, including source, locator, content SHA-256, collection time, complete canonical evidence-reference JSON, and a deterministic SHA-256 hash of that reference metadata. The locator is not treated as integrity proof; evidence bytes are not stored in this table.

`observation_evidence` links observations to evidence references using tenant-qualified composite keys and foreign keys. Future immutable evidence-content storage is separate from these evidence-reference metadata rows.
