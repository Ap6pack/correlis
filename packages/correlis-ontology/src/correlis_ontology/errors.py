from __future__ import annotations

from typing import Any


class OntologyConfigurationError(Exception):
    """Raised when the immutable ontology registry is incomplete or contradictory."""


class OntologyValidationError(ValueError):
    """Raised when a record violates the stable ontology contract."""

    def __init__(self, code: str, message: str, **attributes: Any) -> None:
        super().__init__(message)
        self.code = code
        self.attributes = attributes
