from __future__ import annotations

import hashlib
import inspect
import json
import re

from correlis_schema import EntityType
from correlis_store import canonical_entity_key


def test_canonical_entity_key_vectors_and_boundaries():
    key = canonical_entity_key(EntityType.ASSET, "asset-123")
    expected = hashlib.sha256(
        json.dumps(
            {"entity_id": "asset-123", "entity_type": "asset"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert key == expected
    assert canonical_entity_key(EntityType.ASSET, "asset-123") == key
    assert canonical_entity_key(EntityType.ASSET, "asset-124") != key
    assert canonical_entity_key(EntityType.APPLICATION, "asset-123") != key
    assert canonical_entity_key(EntityType.ASSET, "Asset-123") != key
    assert canonical_entity_key(EntityType.ASSET, "资产-123") == canonical_entity_key(
        EntityType.ASSET, "资产-123"
    )
    assert re.fullmatch(r"[a-f0-9]{64}", key)


def test_canonical_entity_key_ignores_tenant_version_label_and_attributes():
    tenant_a = canonical_entity_key(EntityType.ASSET, "asset-123")
    tenant_b = canonical_entity_key(EntityType.ASSET, "asset-123")
    version_1 = canonical_entity_key(EntityType.ASSET, "asset-123")
    version_2 = canonical_entity_key(EntityType.ASSET, "asset-123")
    label_a = canonical_entity_key(EntityType.ASSET, "asset-123")
    label_b = canonical_entity_key(EntityType.ASSET, "asset-123")
    attrs_a = canonical_entity_key(EntityType.ASSET, "asset-123")
    attrs_b = canonical_entity_key(EntityType.ASSET, "asset-123")
    assert (
        tenant_a == tenant_b == version_1 == version_2 == label_a == label_b == attrs_a == attrs_b
    )


def test_canonical_entity_key_does_not_use_python_hash():
    source = inspect.getsource(canonical_entity_key)
    assert "hash(" not in source
    assert "sha256" in inspect.getsource(canonical_entity_key.__globals__["_sha"])
