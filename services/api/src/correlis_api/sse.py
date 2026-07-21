from __future__ import annotations

import json


def _safe(value: str, field: str) -> None:
    if field == "event" and not value:
        raise ValueError("invalid SSE event")
    if any(ch in value for ch in ("\r", "\n", "\x00")):
        raise ValueError(f"invalid SSE {field}")


def encode_sse_event(
    *, event: str, data: dict[str, object], event_id: str | None = None, retry_ms: int | None = None
) -> bytes:
    _safe(event, "event")
    lines: list[str] = []
    if event_id is not None:
        _safe(event_id, "id")
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    if retry_ms is not None:
        if not isinstance(retry_ms, int) or isinstance(retry_ms, bool) or retry_ms < 0:
            raise ValueError("invalid SSE retry")
        lines.append(f"retry: {retry_ms}")
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def encode_sse_comment(comment: str = "keepalive") -> bytes:
    _safe(comment, "comment")
    return f": {comment}\n\n".encode()
