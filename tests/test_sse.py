import pytest
from correlis_api.sse import encode_sse_comment, encode_sse_event


def test_ready_event_formatting_and_terminator():
    assert encode_sse_event(event="ready", retry_ms=0, data={"b": 2, "a": 1}) == (
        b"event: ready\nretry: 0\ndata: {\"a\":1,\"b\":2}\n\n"
    )


def test_event_id_and_utf8_json():
    assert encode_sse_event(event="observation", event_id="cursor", data={"text": "caf\u00e9"}) == (
        "id: cursor\nevent: observation\ndata: {\"text\":\"caf\u00e9\"}\n\n".encode()
    )


@pytest.mark.parametrize("event", ["", "bad\nname", "bad\rname", "bad\x00name"])
def test_invalid_event_names_rejected(event):
    with pytest.raises(ValueError):
        encode_sse_event(event=event, data={})


@pytest.mark.parametrize("event_id", ["bad\nid", "bad\rid", "bad\x00id"])
def test_invalid_event_ids_rejected(event_id):
    with pytest.raises(ValueError):
        encode_sse_event(event="checkpoint", event_id=event_id, data={})


@pytest.mark.parametrize("retry", [-1, True, 1.5])
def test_invalid_retry_rejected(retry):
    with pytest.raises(ValueError):
        encode_sse_event(event="stream_error", retry_ms=retry, data={})


def test_heartbeat_formatting():
    assert encode_sse_comment() == b": keepalive\n\n"
