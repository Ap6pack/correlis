from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_model_json(model: BaseModel) -> dict:
    return model.model_dump(mode="json")


def canonical_model_sha256(model: BaseModel) -> str:
    payload = json.dumps(
        canonical_model_json(model), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
