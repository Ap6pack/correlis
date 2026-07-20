from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from correlis_api.pagination import (
    PaginationCursorError,
    decode_cursor,
    encode_cursor,
    query_filter_fingerprint,
)
from correlis_store import ObservationPageAnchor


def test_cursor_round_trip_deterministic_and_opaque():
    anchor = ObservationPageAnchor(datetime(2026, 7, 20, 3, tzinfo=UTC), "obs-123")
    fp = query_filter_fingerprint(tenant_id="tenant-a", source="src", event_class="authentication")
    cursor = encode_cursor(anchor, fp)
    assert cursor == encode_cursor(anchor, fp)
    decoded, decoded_fp = decode_cursor(cursor)
    assert decoded == anchor
    assert decoded_fp == fp
    assert "tenant-a" not in cursor
    assert "credential" not in cursor
    decode_cursor(cursor.rstrip("="))


def test_cursor_rejects_invalid_payloads():
    with pytest.raises(PaginationCursorError):
        decode_cursor("v2.abc")
    with pytest.raises(PaginationCursorError):
        decode_cursor("v1.!!!!")
    raw = base64.urlsafe_b64encode(b'{"event_time":"2026-01-01T00:00:00Z"}').decode().rstrip("=")
    with pytest.raises(PaginationCursorError):
        decode_cursor("v1." + raw)
    payload = {
        "event_time": "2026-01-01T00:00:00",
        "observation_id": "obs",
        "filter_fingerprint": "fp",
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(PaginationCursorError):
        decode_cursor("v1." + raw)
    payload["event_time"] = "2026-01-01T00:00:00Z"
    payload["observation_id"] = ""
    payload["extra"] = 1
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(PaginationCursorError):
        decode_cursor("v1." + raw)
    with pytest.raises(PaginationCursorError):
        decode_cursor("v1." + "a" * 4097)


def test_filter_fingerprint_changes_only_for_bound_filters():
    base = query_filter_fingerprint(
        tenant_id="t",
        source="s",
        event_time_from=datetime(2026, 1, 1, tzinfo=UTC),
        event_time_to=datetime(2026, 1, 2, tzinfo=UTC),
        event_class="authentication",
        severity="low",
        sensor_id="sensor",
    )
    assert base == query_filter_fingerprint(
        tenant_id="t",
        source="s",
        event_time_from=datetime(2026, 1, 1, tzinfo=UTC),
        event_time_to=datetime(2026, 1, 2, tzinfo=UTC),
        event_class="authentication",
        severity="low",
        sensor_id="sensor",
    )
    assert base != query_filter_fingerprint(
        tenant_id="t2", source="s", event_class="authentication", severity="low", sensor_id="sensor"
    )
    assert base != query_filter_fingerprint(
        tenant_id="t", source="s2", event_class="authentication", severity="low", sensor_id="sensor"
    )
    assert base != query_filter_fingerprint(
        tenant_id="t",
        source="s",
        event_class="network_activity",
        severity="low",
        sensor_id="sensor",
    )
