from __future__ import annotations

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

INGESTION_PATHS = {"/api/v1/observations", "/api/v1/observations/batch"}


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin1")
    return None


def _is_json(content_type: str | None) -> bool:
    if not content_type:
        return False
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


class IngestionBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in INGESTION_PATHS
        ):
            await self.app(scope, receive, send)
            return
        request_id = scope.setdefault("state", {}).get("request_id")
        if not _is_json(_header(scope, b"content-type")):
            await self._reject(
                send,
                415,
                {
                    "code": "unsupported_media_type",
                    "message": "Observation ingestion requires a JSON request body.",
                },
                request_id,
            )
            return
        content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._too_large(send, request_id)
                    return
            except ValueError:
                pass
        seen = 0
        exceeded = False

        async def limited_receive() -> Message:
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                seen += len(body)
                if seen > self.max_body_bytes:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Message) -> None:
            if not exceeded:
                await send(message)

        await self.app(scope, limited_receive, guarded_send)
        if exceeded:
            await self._too_large(send, request_id)

    async def _too_large(self, send: Send, request_id: str | None) -> None:
        await self._reject(
            send,
            413,
            {
                "code": "request_body_too_large",
                "message": "The request body exceeds the configured ingestion limit.",
                "max_bytes": self.max_body_bytes,
            },
            request_id,
        )

    async def _reject(
        self, send: Send, status: int, detail: dict[str, Any], request_id: str | None
    ) -> None:
        body = json.dumps({"detail": detail}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if request_id:
            headers.append((b"x-request-id", request_id.encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
