from __future__ import annotations

from collections.abc import Iterator

from correlis_ontology import OntologyRegistry
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .scenarios import ScenarioRepository

ONTOLOGY_NOT_CONFIGURED_DETAIL = {
    "code": "ontology_not_configured",
    "message": "Ontology registry is not configured.",
}

DATABASE_NOT_CONFIGURED_DETAIL = {
    "code": "database_not_configured",
    "message": "Database services are not configured.",
}


def get_scenario_repository(request: Request) -> ScenarioRepository:
    return request.app.state.scenario_repository


def get_ontology_registry(request: Request) -> OntologyRegistry:
    registry = getattr(request.app.state, "ontology_registry", None)
    if registry is None:
        raise HTTPException(status_code=500, detail=ONTOLOGY_NOT_CONFIGURED_DETAIL)
    return registry


def get_database_session(request: Request) -> Iterator[Session]:
    session_factory = getattr(request.app.state, "database_session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=503, detail=DATABASE_NOT_CONFIGURED_DETAIL)

    session: Session = session_factory()
    try:
        yield session
    except Exception:
        if session.in_transaction():
            session.rollback()
        raise
    finally:
        session.close()
