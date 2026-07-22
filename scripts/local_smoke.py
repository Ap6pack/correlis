from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://localhost:8080"


@dataclass(frozen=True, slots=True)
class SmokeEndpoint:
    name: str
    path: str


ENDPOINTS = (
    SmokeEndpoint("liveness", "/health/live"),
    SmokeEndpoint("readiness", "/health/ready"),
    SmokeEndpoint("ontology", "/api/v1/ontology"),
    SmokeEndpoint("scenario list", "/api/v1/scenarios"),
    SmokeEndpoint("built attack scene", "/api/v1/scenarios/initial-access-demo/scene"),
)


def _request_json(base_url: str, endpoint: SmokeEndpoint) -> Any:
    url = f"{base_url.rstrip('/')}{endpoint.path}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - local operator-selected URL
            body = response.read().decode("utf-8")
            if response.status != 200:
                raise RuntimeError(f"returned HTTP {response.status}")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"could not connect: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("returned a non-JSON response") from exc


def main() -> int:
    base_url = os.environ.get("CORRELIS_BASE_URL", DEFAULT_BASE_URL)
    failures: list[str] = []

    for endpoint in ENDPOINTS:
        try:
            _request_json(base_url, endpoint)
        except RuntimeError as exc:
            failures.append(f"[fail] {endpoint.name}: {exc}")
        else:
            print(f"[ok] {endpoint.name}: {base_url.rstrip('/')}{endpoint.path}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"Correlis smoke test passed against {base_url.rstrip('/')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
