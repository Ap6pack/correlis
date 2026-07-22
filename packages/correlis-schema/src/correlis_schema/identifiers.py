from __future__ import annotations

import hashlib

from .models import ProvenanceClass, RelationshipType


def relationship_id(
    tenant_id: str,
    source_id: str,
    relationship_type: RelationshipType,
    target_id: str,
    provenance: ProvenanceClass,
    rule_id: str | None = None,
) -> str:
    material = "|".join(
        [
            tenant_id,
            source_id,
            relationship_type.value,
            target_id,
            provenance.value,
            rule_id or "direct",
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]
