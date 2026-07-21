from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable

from correlis_store.credential_security import validate_credential_pepper
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DOMAIN = b"correlis-observation-stream-cursor-v1"
PREFIX = "ocs1."
MAX_CURSOR_LENGTH = 4096
_FIELDS = {"collector_id", "position", "source", "tenant_id"}


class ObservationStreamCursorError(ValueError):
    pass


class ObservationStreamCursorCodec:
    def __init__(self, credential_pepper: str, *, nonce_factory: Callable[[], bytes] | None = None):
        validate_credential_pepper(credential_pepper)
        self._key = HKDF(algorithm=SHA256(), length=32, salt=None, info=DOMAIN).derive(
            credential_pepper.encode("utf-8")
        )
        self._nonce_factory = nonce_factory or (lambda: os.urandom(12))

    def encode(self, *, position: int, tenant_id: str, collector_id: str, source: str) -> str:
        self._validate_payload(position, tenant_id, collector_id, source)
        nonce = self._nonce_factory()
        if len(nonce) != 12:
            raise ValueError("nonce factory must return 12 bytes")
        payload = json.dumps(
            {
                "collector_id": collector_id,
                "position": position,
                "source": source,
                "tenant_id": tenant_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = nonce + AESGCM(self._key).encrypt(nonce, payload, DOMAIN)
        return PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii").rstrip("=")

    def decode(self, token: str, *, tenant_id: str, collector_id: str, source: str) -> int:
        try:
            if not isinstance(token, str) or not token or len(token) > MAX_CURSOR_LENGTH:
                raise ValueError
            if token != token.strip() or any(ord(ch) < 32 or ch == "\x7f" for ch in token):
                raise ValueError
            if not token.startswith(PREFIX):
                raise ValueError
            raw = token[len(PREFIX) :]
            padded = raw + "=" * (-len(raw) % 4)
            encrypted = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
            if len(encrypted) <= 12:
                raise ValueError
            nonce, ciphertext = encrypted[:12], encrypted[12:]
            payload = AESGCM(self._key).decrypt(nonce, ciphertext, DOMAIN)
            data = json.loads(payload.decode("utf-8"))
            if set(data) != _FIELDS:
                raise ValueError
            position = data["position"]
            if not isinstance(position, int) or isinstance(position, bool):
                raise ValueError
            self._validate_payload(
                position, data["tenant_id"], data["collector_id"], data["source"]
            )
            if (
                data["tenant_id"] != tenant_id
                or data["collector_id"] != collector_id
                or data["source"] != source
            ):
                raise ValueError
            return position
        except Exception as exc:
            raise ObservationStreamCursorError("invalid observation stream cursor") from exc

    @staticmethod
    def _validate_payload(position: int, tenant_id: str, collector_id: str, source: str) -> None:
        if not isinstance(position, int) or isinstance(position, bool) or position < 0:
            raise ValueError("invalid position")
        for value in (tenant_id, collector_id, source):
            if not isinstance(value, str) or not value:
                raise ValueError("invalid scope")
