from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field

TOKEN_PREFIX = "correlis_v1"
TOKEN_VERSION = "v1"
DOMAIN = b"correlis-collector-credential-v1"
MIN_PEPPER_BYTES = 32
SECRET_RANDOM_BYTES = 32


class CredentialPepperConfigurationError(ValueError):
    pass


class TokenParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedCollectorToken:
    credential_id: str
    secret: str = field(repr=False)
    token_version: str = TOKEN_VERSION


def validate_credential_pepper(pepper: str | None) -> bytes:
    if not pepper:
        raise CredentialPepperConfigurationError("credential pepper is not configured")
    encoded = pepper.encode("utf-8")
    if len(encoded) < MIN_PEPPER_BYTES:
        raise CredentialPepperConfigurationError("credential pepper is too weak")
    return encoded


def generate_collector_token() -> tuple[str, str, str]:
    credential_id = str(uuid.uuid4())
    secret = secrets.token_urlsafe(SECRET_RANDOM_BYTES)
    return credential_id, secret, f"{TOKEN_PREFIX}.{credential_id}.{secret}"


def parse_collector_token(token: str) -> ParsedCollectorToken:
    try:
        prefix, credential_id, secret = token.split(".")
    except ValueError as exc:
        raise TokenParseError("malformed collector token") from exc
    if prefix != TOKEN_PREFIX or not secret:
        raise TokenParseError("malformed collector token")
    try:
        parsed_uuid = uuid.UUID(credential_id)
    except ValueError as exc:
        raise TokenParseError("malformed collector token") from exc
    if str(parsed_uuid) != credential_id:
        raise TokenParseError("malformed collector token")
    return ParsedCollectorToken(credential_id=credential_id, secret=secret)


def credential_digest(
    *, pepper: str, credential_id: str, secret: str, token_version: str = TOKEN_VERSION
) -> str:
    key = validate_credential_pepper(pepper)
    msg = b"\x00".join([DOMAIN, token_version.encode(), credential_id.encode(), secret.encode()])
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_credential_digest(
    *,
    pepper: str,
    credential_id: str,
    secret: str,
    expected_digest: str,
    token_version: str = TOKEN_VERSION,
) -> bool:
    actual = credential_digest(
        pepper=pepper, credential_id=credential_id, secret=secret, token_version=token_version
    )
    return hmac.compare_digest(actual, expected_digest)
