from __future__ import annotations

import logging
from typing import Annotated, Literal

from correlis_ontology import OntologyRegistry
from correlis_ontology.errors import OntologyValidationError
from correlis_schema import Observation
from correlis_store import (
    AuthenticatedCollectorPrincipal,
    ImmutableRecordConflict,
    ObservationRepository,
    WriteDisposition,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .collector_auth import get_authenticated_collector
from .dependencies import get_database_session, get_ontology_registry
from .request_context import get_request_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["observation-ingestion"])


class SingleIngestionResponse(BaseModel):
    request_id: str
    tenant_id: str
    collector_id: str
    observation_id: str
    source: str
    disposition: Literal["created", "existing"]


class ObservationBatchRequest(BaseModel):
    observations: list[Observation] = Field(min_length=1)


class BatchConflict(BaseModel):
    resource_type: Literal["observation", "evidence"]
    record_id: str


class BatchItemResult(BaseModel):
    index: int
    observation_id: str
    disposition: Literal["created", "existing", "conflict"]
    conflict: BatchConflict | None = None


class BatchSummary(BaseModel):
    total: int
    created: int
    existing: int
    conflict: int


class BatchIngestionResponse(BaseModel):
    request_id: str
    tenant_id: str
    collector_id: str
    source: str
    summary: BatchSummary
    results: list[BatchItemResult]


def _scope_check(
    observation: Observation, principal: AuthenticatedCollectorPrincipal, index: int | None = None
) -> Observation:
    if observation.tenant_id != principal.tenant_id:
        detail = {
            "code": "collector_tenant_scope_mismatch",
            "message": "The observation is outside the authenticated collector tenant scope.",
        }
        if index is not None:
            detail["item_index"] = index
        raise HTTPException(status_code=403, detail=detail)
    if observation.source != principal.source:
        detail = {
            "code": "collector_source_scope_mismatch",
            "message": (
                "The observation source is outside the authenticated collector source scope."
            ),
        }
        if index is not None:
            detail["item_index"] = index
        raise HTTPException(status_code=403, detail=detail)
    return observation.model_copy(
        update={"tenant_id": principal.tenant_id, "source": principal.source}
    )


def _ontology_error(exc: OntologyValidationError, index: int | None = None) -> HTTPException:
    detail = {
        "code": "ontology_validation_failed",
        "message": "The observation violates the configured ontology.",
        "ontology_code": exc.code,
    }
    if index is not None:
        detail["item_index"] = index
    return HTTPException(status_code=422, detail=detail)


def _conflict_detail(exc: ImmutableRecordConflict) -> dict[str, str]:
    if exc.resource_type == "observation":
        return {
            "code": "immutable_observation_conflict",
            "message": (
                "The observation identifier already exists with a different immutable payload."
            ),
            "record_id": exc.record_id,
        }
    return {
        "code": "immutable_evidence_conflict",
        "message": "An evidence identifier already exists with different immutable metadata.",
        "record_id": exc.record_id,
    }


def _conflict_type(exc: ImmutableRecordConflict) -> Literal["observation", "evidence"]:
    return "observation" if exc.resource_type == "observation" else "evidence"


@router.post(
    "/observations",
    response_model=SingleIngestionResponse,
    operation_id="ingest_observation",
    status_code=201,
)
def ingest_observation(
    observation: Observation,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedCollectorPrincipal, Depends(get_authenticated_collector)],
    session: Annotated[Session, Depends(get_database_session)],
    ontology_registry: Annotated[OntologyRegistry, Depends(get_ontology_registry)],
) -> SingleIngestionResponse:
    request_id = get_request_id(request)
    trusted = _scope_check(observation, principal)
    try:
        ontology_registry.validate_observation(trusted)
        disposition = ObservationRepository(session).put(trusted)
    except OntologyValidationError as exc:
        raise _ontology_error(exc) from exc
    except ImmutableRecordConflict as exc:
        raise HTTPException(status_code=409, detail=_conflict_detail(exc)) from exc
    except Exception as exc:
        logger.exception(
            "observation store unavailable",
            extra={
                "request_id": request_id,
                "tenant_id": principal.tenant_id,
                "collector_id": principal.collector_id,
                "observation_id": trusted.id,
            },
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "observation_store_unavailable",
                "message": "The observation store is temporarily unavailable.",
            },
        ) from exc
    if disposition == WriteDisposition.EXISTING:
        response.status_code = 200
    logger.info(
        "observation ingested",
        extra={
            "request_id": request_id,
            "tenant_id": principal.tenant_id,
            "collector_id": principal.collector_id,
            "observation_id": trusted.id,
            "disposition": disposition.value,
        },
    )
    return SingleIngestionResponse(
        request_id=request_id,
        tenant_id=principal.tenant_id,
        collector_id=principal.collector_id,
        observation_id=trusted.id,
        source=principal.source,
        disposition=disposition.value,
    )


@router.post(
    "/observations/batch",
    response_model=BatchIngestionResponse,
    operation_id="ingest_observation_batch",
)
def ingest_observation_batch(
    batch: ObservationBatchRequest,
    request: Request,
    principal: Annotated[AuthenticatedCollectorPrincipal, Depends(get_authenticated_collector)],
    session: Annotated[Session, Depends(get_database_session)],
    ontology_registry: Annotated[OntologyRegistry, Depends(get_ontology_registry)],
) -> BatchIngestionResponse:
    request_id = get_request_id(request)
    max_items = request.app.state.settings.ingest_max_batch_size
    if len(batch.observations) > max_items:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "batch_size_exceeded",
                "message": "The observation batch exceeds the configured item limit.",
                "max_items": max_items,
            },
        )
    trusted_items = []
    for index, observation in enumerate(batch.observations):
        trusted = _scope_check(observation, principal, index)
        try:
            ontology_registry.validate_observation(trusted)
        except OntologyValidationError as exc:
            raise _ontology_error(exc, index) from exc
        trusted_items.append(trusted)
    results: list[BatchItemResult] = []
    counts = {"created": 0, "existing": 0, "conflict": 0}
    repo = ObservationRepository(session)
    for index, trusted in enumerate(trusted_items):
        try:
            disposition = repo.put(trusted).value
            counts[disposition] += 1
            results.append(
                BatchItemResult(index=index, observation_id=trusted.id, disposition=disposition)
            )
        except ImmutableRecordConflict as exc:
            counts["conflict"] += 1
            results.append(
                BatchItemResult(
                    index=index,
                    observation_id=trusted.id,
                    disposition="conflict",
                    conflict=BatchConflict(
                        resource_type=_conflict_type(exc), record_id=exc.record_id
                    ),
                )
            )
        except Exception as exc:
            logger.exception(
                "observation store unavailable",
                extra={
                    "request_id": request_id,
                    "tenant_id": principal.tenant_id,
                    "collector_id": principal.collector_id,
                    "item_index": index,
                },
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "observation_store_unavailable",
                    "message": "The observation store is temporarily unavailable.",
                },
            ) from exc
    logger.info(
        "observation batch ingested",
        extra={
            "request_id": request_id,
            "tenant_id": principal.tenant_id,
            "collector_id": principal.collector_id,
            "batch_count": len(trusted_items),
            "created_count": counts["created"],
            "existing_count": counts["existing"],
            "conflict_count": counts["conflict"],
        },
    )
    return BatchIngestionResponse(
        request_id=request_id,
        tenant_id=principal.tenant_id,
        collector_id=principal.collector_id,
        source=principal.source,
        summary=BatchSummary(
            total=len(trusted_items),
            created=counts["created"],
            existing=counts["existing"],
            conflict=counts["conflict"],
        ),
        results=results,
    )
