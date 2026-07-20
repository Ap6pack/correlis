from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from correlis_store import ObservationPageAnchor

MAX_CURSOR_LENGTH = 4096
VERSION = "v1"


class PaginationCursorError(ValueError):
    pass


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def query_filter_fingerprint(
    *,
    tenant_id: str,
    source: str,
    event_time_from: datetime | None = None,
    event_time_to: datetime | None = None,
    event_class: Any = None,
    severity: Any = None,
    sensor_id: str | None = None,
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "source": source,
        "event_time_from": _dt(event_time_from),
        "event_time_to": _dt(event_time_to),
        "event_class": str(event_class) if event_class is not None else None,
        "severity": str(severity) if severity is not None else None,
        "sensor_id": sensor_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def encode_cursor(anchor: ObservationPageAnchor, filter_fingerprint: str) -> str:
    payload = {
        "event_time": _dt(anchor.event_time),
        "filter_fingerprint": filter_fingerprint,
        "observation_id": anchor.observation_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{VERSION}." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[ObservationPageAnchor, str]:
    try:
        if len(cursor) > MAX_CURSOR_LENGTH or not cursor.startswith(f"{VERSION}."):
            raise PaginationCursorError()
        encoded = cursor.split(".", 1)[1]
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw)
        if set(payload) != {"event_time", "observation_id", "filter_fingerprint"}:
            raise PaginationCursorError()
        if not isinstance(payload["observation_id"], str) or not payload["observation_id"]:
            raise PaginationCursorError()
        if not isinstance(payload["filter_fingerprint"], str) or not payload["filter_fingerprint"]:
            raise PaginationCursorError()
        event_time = datetime.fromisoformat(str(payload["event_time"]).replace("Z", "+00:00"))
        if event_time.tzinfo is None or event_time.utcoffset() is None:
            raise PaginationCursorError()
        return ObservationPageAnchor(
            event_time=event_time, observation_id=payload["observation_id"]
        ), payload["filter_fingerprint"]
    except Exception as exc:
        if isinstance(exc, PaginationCursorError):
            raise
        raise PaginationCursorError() from exc
