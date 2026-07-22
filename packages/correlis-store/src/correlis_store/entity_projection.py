from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from correlis_ontology import CORE_ONTOLOGY, OntologyRegistry
from correlis_schema import EntityRef, EntityType
from sqlalchemy.orm import Session

from .models import (
    EntityEvidenceRecord,
    EntityIdentityClaimRecord,
    EntityObservationRecord,
    EntityRecord,
)
from .observation_sequence import SequencedObservation
from .projections import ProjectionHandlerError, ProjectorIdentity

ENTITY_PROJECTOR_NAME = "entity-projection"
DEFAULT_ENTITY_PROJECTOR_VERSION = "1"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def entity_projector_identity(version: str = DEFAULT_ENTITY_PROJECTOR_VERSION) -> ProjectorIdentity:
    return ProjectorIdentity(ENTITY_PROJECTOR_NAME, version)


def canonical_entity_key(entity_type: EntityType, entity_id: str) -> str:
    return _sha({"entity_id": entity_id, "entity_type": entity_type.value})


def _aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _clock() -> datetime:
    return datetime.now(UTC)


class EntityProjectionHandler:
    def __init__(
        self,
        *,
        projection_version: str = DEFAULT_ENTITY_PROJECTOR_VERSION,
        ontology_registry: OntologyRegistry = CORE_ONTOLOGY,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._projection_version = projection_version
        self._registry = ontology_registry
        self._clock = clock or _clock

    @property
    def projector_identity(self) -> ProjectorIdentity:
        return entity_projector_identity(self._projection_version)

    def __call__(self, session: Session, item: SequencedObservation) -> None:
        obs = item.observation
        if not _aware(obs.event_time):
            raise ProjectionHandlerError(
                "entity_event_time_timezone_required",
                "Entity projection requires timezone-aware observation event times.",
            )
        for ref in (obs.subject, obs.object):
            if ref is not None:
                self._registry.validate_entity(ref)
        by_id: dict[str, list[tuple[str, EntityRef]]] = defaultdict(list)
        by_id[obs.subject.id].append(("subject", obs.subject))
        if obs.object is not None:
            by_id[obs.object.id].append(("object", obs.object))
        refs: list[tuple[EntityRef, set[str]]] = []
        for _, entries in by_id.items():
            base = entries[0][1]
            roles = {role for role, _ in entries}
            for _, ref in entries[1:]:
                if (
                    ref.type != base.type
                    or ref.label != base.label
                    or _canonical_json(ref.attributes) != _canonical_json(base.attributes)
                ):
                    raise ProjectionHandlerError(
                        "entity_reference_conflict",
                        "An observation contains conflicting references for the same "
                        "entity identifier.",
                    )
            refs.append((base, roles))
        now = self._clock()
        for ref, roles in refs:
            rec = self._upsert_entity(session, item, ref, now)
            for role in roles:
                if (
                    session.get(
                        EntityObservationRecord,
                        {
                            "projection_version": self._projection_version,
                            "tenant_id": obs.tenant_id,
                            "entity_id": ref.id,
                            "observation_id": obs.id,
                            "role": role,
                        },
                    )
                    is None
                ):
                    session.add(
                        EntityObservationRecord(
                            projection_version=self._projection_version,
                            tenant_id=obs.tenant_id,
                            entity_id=ref.id,
                            observation_id=obs.id,
                            role=role,
                            ingest_sequence=item.ingest_sequence,
                            event_time=obs.event_time,
                            created_at=now,
                        )
                    )
            for evidence in obs.evidence:
                self._upsert_evidence(session, item, ref.id, evidence.id, now)
            for name, value, digest in self._claims(ref):
                self._upsert_claim(session, item, rec.entity_type, ref.id, name, value, digest, now)
        session.flush()

    def _upsert_entity(
        self, session: Session, item: SequencedObservation, ref: EntityRef, now: datetime
    ) -> EntityRecord:
        obs = item.observation
        key = canonical_entity_key(ref.type, ref.id)
        pk = {
            "projection_version": self._projection_version,
            "tenant_id": obs.tenant_id,
            "entity_id": ref.id,
        }
        rec = session.get(EntityRecord, pk)
        if rec is None:
            rec = EntityRecord(
                projection_version=self._projection_version,
                tenant_id=obs.tenant_id,
                entity_id=ref.id,
                canonical_key=key,
                entity_type=ref.type.value,
                label=ref.label,
                attributes_json=dict(ref.attributes),
                ontology_name=self._registry.name,
                ontology_version=self._registry.version,
                first_seen=obs.event_time,
                last_seen=obs.event_time,
                first_ingest_sequence=item.ingest_sequence,
                last_ingest_sequence=item.ingest_sequence,
                latest_claim_event_time=obs.event_time,
                latest_claim_ingest_sequence=item.ingest_sequence,
                created_at=now,
                updated_at=now,
            )
            session.add(rec)
            return rec
        if rec.entity_type != ref.type.value or rec.canonical_key != key:
            raise ProjectionHandlerError(
                "entity_type_conflict",
                "Entity identifier was observed with a conflicting entity type.",
            )
        if (
            rec.ontology_name != self._registry.name
            or rec.ontology_version != self._registry.version
        ):
            raise ProjectionHandlerError(
                "entity_projection_ontology_mismatch",
                "Entity projection version does not match the stored ontology version.",
            )
        changed = False
        if obs.event_time < rec.first_seen:
            rec.first_seen = obs.event_time
            changed = True
        if obs.event_time > rec.last_seen:
            rec.last_seen = obs.event_time
            changed = True
        if item.ingest_sequence < rec.first_ingest_sequence:
            rec.first_ingest_sequence = item.ingest_sequence
            changed = True
        if item.ingest_sequence > rec.last_ingest_sequence:
            rec.last_ingest_sequence = item.ingest_sequence
            changed = True
        if (obs.event_time, item.ingest_sequence) > (
            rec.latest_claim_event_time,
            rec.latest_claim_ingest_sequence,
        ):
            rec.label = ref.label
            rec.attributes_json = dict(ref.attributes)
            rec.latest_claim_event_time = obs.event_time
            rec.latest_claim_ingest_sequence = item.ingest_sequence
            changed = True
        if changed:
            rec.updated_at = now
        return rec

    def _upsert_evidence(
        self,
        session: Session,
        item: SequencedObservation,
        entity_id: str,
        evidence_id: str,
        now: datetime,
    ) -> None:
        obs = item.observation
        pk = {
            "projection_version": self._projection_version,
            "tenant_id": obs.tenant_id,
            "entity_id": entity_id,
            "evidence_id": evidence_id,
        }
        rec = session.get(EntityEvidenceRecord, pk)
        if rec is None:
            session.add(
                EntityEvidenceRecord(
                    **pk,
                    first_seen=obs.event_time,
                    last_seen=obs.event_time,
                    first_ingest_sequence=item.ingest_sequence,
                    last_ingest_sequence=item.ingest_sequence,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        changed = False
        if obs.event_time < rec.first_seen:
            rec.first_seen = obs.event_time
            changed = True
        if obs.event_time > rec.last_seen:
            rec.last_seen = obs.event_time
            changed = True
        if item.ingest_sequence < rec.first_ingest_sequence:
            rec.first_ingest_sequence = item.ingest_sequence
            changed = True
        if item.ingest_sequence > rec.last_ingest_sequence:
            rec.last_ingest_sequence = item.ingest_sequence
            changed = True
        if changed:
            rec.updated_at = now

    def _claims(self, ref: EntityRef):
        for definition in self._registry.get_entity_definition(ref.type).identity_keys:
            value = {}
            skip = False
            for field in definition.fields:
                if field not in ref.attributes or not self._valid_claim_scalar(
                    ref.attributes[field]
                ):
                    skip = True
                    break
                value[field] = ref.attributes[field]
            if not skip:
                yield definition.name, value, _sha(value)

    def _valid_claim_scalar(self, v: Any) -> bool:
        if v is None or isinstance(v, bool):
            return v is not None
        if isinstance(v, str):
            return bool(v.strip())
        if isinstance(v, int):
            return True
        if isinstance(v, float):
            return math.isfinite(v)
        return False

    def _upsert_claim(
        self,
        session: Session,
        item: SequencedObservation,
        entity_type: str,
        entity_id: str,
        name: str,
        value: dict[str, Any],
        digest: str,
        now: datetime,
    ) -> None:
        obs = item.observation
        pk = {
            "projection_version": self._projection_version,
            "tenant_id": obs.tenant_id,
            "entity_id": entity_id,
            "identity_key_name": name,
            "value_sha256": digest,
        }
        rec = session.get(EntityIdentityClaimRecord, pk)
        if rec is None:
            session.add(
                EntityIdentityClaimRecord(
                    **pk,
                    entity_type=entity_type,
                    value_json=value,
                    first_seen=obs.event_time,
                    last_seen=obs.event_time,
                    first_ingest_sequence=item.ingest_sequence,
                    last_ingest_sequence=item.ingest_sequence,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        changed = False
        if obs.event_time < rec.first_seen:
            rec.first_seen = obs.event_time
            changed = True
        if obs.event_time > rec.last_seen:
            rec.last_seen = obs.event_time
            changed = True
        if item.ingest_sequence < rec.first_ingest_sequence:
            rec.first_ingest_sequence = item.ingest_sequence
            changed = True
        if item.ingest_sequence > rec.last_ingest_sequence:
            rec.last_ingest_sequence = item.ingest_sequence
            changed = True
        if changed:
            rec.updated_at = now
