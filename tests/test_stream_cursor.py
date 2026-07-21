import pytest
from correlis_api.stream_cursor import (
    DOMAIN,
    ObservationStreamCursorCodec,
    ObservationStreamCursorError,
)

PEPPER = "non-production-test-pepper-value-32-bytes"
SCOPE = {"tenant_id": "tenant", "collector_id": "collector", "source": "src"}


def codec(nonce=b"0" * 12):
    return ObservationStreamCursorCodec(PEPPER, nonce_factory=lambda: nonce)


def test_positions_round_trip_and_scope_enforced():
    token = codec().encode(position=42, **SCOPE)
    assert codec().decode(token, **SCOPE) == 42
    wrong_scopes = [
        {"tenant_id": "x", "collector_id": "collector", "source": "src"},
        {"tenant_id": "tenant", "collector_id": "x", "source": "src"},
        {"tenant_id": "tenant", "collector_id": "collector", "source": "x"},
    ]
    for kwargs in wrong_scopes:
        with pytest.raises(ObservationStreamCursorError):
            codec().decode(token, **kwargs)


def test_tokens_are_encrypted_and_nonce_unique():
    first = codec(b"1" * 12).encode(position=0, **SCOPE)
    second = codec(b"2" * 12).encode(position=0, **SCOPE)
    assert first != second
    assert all(clear not in first for clear in ["tenant", "collector", "src"])
    assert codec().decode(first, **SCOPE) == 0
    assert codec().decode(second, **SCOPE) == 0


@pytest.mark.parametrize("token", ["", "   ", " ocs1.bad", "ocs1.bad", "x.y", "ocs1.@@@"])
def test_bad_tokens_rejected(token):
    with pytest.raises(ObservationStreamCursorError):
        codec().decode(token, **SCOPE)


def test_bad_encode_values_rejected_and_domain_documented():
    with pytest.raises(ValueError):
        ObservationStreamCursorCodec(PEPPER, nonce_factory=lambda: b"short").encode(
            position=0, tenant_id="t", collector_id="c", source="s"
        )
    with pytest.raises(ValueError):
        codec().encode(position=-1, tenant_id="t", collector_id="c", source="s")
    assert DOMAIN == b"correlis-observation-stream-cursor-v1"
