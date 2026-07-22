from __future__ import annotations

import secrets
import shutil
import sys
from pathlib import Path

MINIMUM_PYTHON = (3, 11)
ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")
PEPPER_PREFIX = "CORRELIS_CREDENTIAL_PEPPER="


def _require_supported_python() -> None:
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        raise SystemExit(f"Correlis requires Python {required}+; found {current}.")


def _ensure_environment_file() -> bool:
    if ENV_PATH.exists():
        return False
    if not ENV_EXAMPLE_PATH.exists():
        raise SystemExit(".env.example is missing; cannot create the local environment file.")
    shutil.copyfile(ENV_EXAMPLE_PATH, ENV_PATH)
    return True


def _ensure_credential_pepper() -> bool:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    changed = False
    found = False

    for index, line in enumerate(lines):
        if not line.startswith(PEPPER_PREFIX):
            continue
        found = True
        if not line.removeprefix(PEPPER_PREFIX).strip():
            lines[index] = f"{PEPPER_PREFIX}{secrets.token_urlsafe(48)}"
            changed = True
        break

    if not found:
        lines.extend(["", f"{PEPPER_PREFIX}{secrets.token_urlsafe(48)}"])
        changed = True

    if changed:
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    _require_supported_python()
    created = _ensure_environment_file()
    generated = _ensure_credential_pepper()
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    print(f"Python {version} is supported.")
    if created:
        print("Created .env from .env.example.")
    else:
        print("Preserved the existing .env file.")
    if generated:
        print("Generated a local collector credential pepper without displaying it.")
    else:
        print("Preserved the existing collector credential pepper.")
    print("Next: run `make local-db`, then `make run`.")
    print("See LOCAL_DEVELOPMENT.md for the complete workflow.")


if __name__ == "__main__":
    main()
