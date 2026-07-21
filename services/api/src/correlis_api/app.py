from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from correlis_ontology import CORE_ONTOLOGY, OntologyManifest, OntologyRegistry
from correlis_store import (
    AuthenticatedCollectorPrincipal,
    CredentialPepperConfigurationError,
    create_database_engine,
    create_session_factory,
)
from correlis_store.credential_security import validate_credential_pepper
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from . import __version__
from .body_limits import IngestionBodyLimitMiddleware
from .collector_auth import get_authenticated_collector
from .dependencies import get_ontology_registry, get_scenario_repository
from .health import (
    check_database_connectivity,
    check_migration_state,
    check_observation_sequence_state,
)
from .ingestion import router as ingestion_router
from .observation_queries import router as observation_queries_router
from .observation_stream import router as observation_stream_router
from .request_context import RequestIdMiddleware
from .scenarios import ScenarioNotFoundError, ScenarioRepository
from .scene import SceneBuilder, build_scene
from .settings import Settings
from .stream_connections import ObservationStreamConnectionLimiter


class LiveHealthResponse(BaseModel):
    status: str
    service: str
    version: str


class DatabaseHealthResponse(BaseModel):
    status: str
    code: str | None = None


class MigrationHealthResponse(BaseModel):
    status: str
    code: str | None = None
    current: list[str] | None = None
    expected: list[str] | None = None


class CollectorAuthHealthResponse(BaseModel):
    status: str
    code: str | None = None


class ObservationSequenceHealthResponse(BaseModel):
    status: str
    high_watermark: int | None = None
    code: str | None = None


class ReadinessChecks(BaseModel):
    database: DatabaseHealthResponse
    migrations: MigrationHealthResponse
    collector_auth: CollectorAuthHealthResponse
    observation_sequence: ObservationSequenceHealthResponse


class ReadinessResponse(BaseModel):
    status: str
    service: str
    version: str
    checks: ReadinessChecks


class CollectorMeResponse(BaseModel):
    tenant_id: str
    collector_id: str
    name: str
    source: str
    credential_id: str


def create_app(
    settings: Settings | None = None,
    *,
    scenario_repository: ScenarioRepository | None = None,
    engine: Engine | None = None,
    ontology_registry: OntologyRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_repository = scenario_repository or ScenarioRepository(resolved_settings.scenario_dir)
    injected_engine = engine
    resolved_ontology_registry = ontology_registry or CORE_ONTOLOGY

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = resolved_settings
        application.state.scenario_repository = resolved_repository
        application.state.ontology_registry = resolved_ontology_registry
        application.state.database_engine = None
        application.state.database_session_factory = None
        application.state.owns_database_engine = False
        application.state.observation_stream_limiter = ObservationStreamConnectionLimiter(
            max_connections=resolved_settings.stream_max_connections,
            max_connections_per_collector=resolved_settings.stream_max_connections_per_collector,
        )

        if injected_engine is not None:
            application.state.database_engine = injected_engine
            application.state.owns_database_engine = False
        elif resolved_settings.database_url:
            application.state.database_engine = create_database_engine(
                resolved_settings.database_url
            )
            application.state.owns_database_engine = True

        if application.state.database_engine is not None:
            application.state.database_session_factory = create_session_factory(
                application.state.database_engine
            )

        try:
            yield
        finally:
            if (
                getattr(application.state, "owns_database_engine", False)
                and application.state.database_engine is not None
            ):
                application.state.database_engine.dispose()
            application.state.database_session_factory = None
            application.state.database_engine = None
            application.state.owns_database_engine = False

    api = FastAPI(
        title="Correlis API",
        version=__version__,
        description="Reference attack-scene contract and replay service.",
        lifespan=lifespan,
    )
    api.state.settings = resolved_settings
    api.state.scenario_repository = resolved_repository
    api.state.ontology_registry = resolved_ontology_registry
    api.state.database_engine = injected_engine
    api.state.database_session_factory = None
    api.state.owns_database_engine = False
    api.state.observation_stream_limiter = ObservationStreamConnectionLimiter(
        max_connections=resolved_settings.stream_max_connections,
        max_connections_per_collector=resolved_settings.stream_max_connections_per_collector,
    )
    api.add_middleware(
        IngestionBodyLimitMiddleware, max_body_bytes=resolved_settings.ingest_max_body_bytes
    )
    api.add_middleware(RequestIdMiddleware)

    @api.exception_handler(RequestValidationError)
    async def ingestion_validation_handler(request: Request, exc: RequestValidationError):
        if request.method == "POST" and request.url.path in {
            "/api/v1/observations",
            "/api/v1/observations/batch",
        }:
            errors = [
                {
                    "location": list(error.get("loc", ())),
                    "type": str(error.get("type", "validation_error")),
                    "message": str(error.get("msg", "Input failed validation")),
                }
                for error in exc.errors()
            ]
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": "request_validation_failed",
                        "message": "The observation request failed validation.",
                        "errors": errors,
                    }
                },
            )

        if request.method == "GET" and request.url.path == "/api/v1/streams/observations":
            errors = [
                {
                    "location": list(error.get("loc", ())),
                    "type": str(error.get("type", "validation_error")),
                    "message": str(error.get("msg", "Input failed validation")),
                }
                for error in exc.errors()
            ]
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": "stream_validation_failed",
                        "message": "The observation stream request failed validation.",
                        "errors": errors,
                    }
                },
            )

        if request.method == "GET" and (
            request.url.path == "/api/v1/observations"
            or request.url.path.startswith("/api/v1/observations/")
            or request.url.path.startswith("/api/v1/evidence/")
        ):
            errors = [
                {
                    "location": list(error.get("loc", ())),
                    "type": str(error.get("type", "validation_error")),
                    "message": str(error.get("msg", "Input failed validation")),
                }
                for error in exc.errors()
            ]
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": "query_validation_failed",
                        "message": "The observation query parameters failed validation.",
                        "errors": errors,
                    }
                },
            )
        return await request_validation_exception_handler(request, exc)

    api.include_router(ingestion_router)
    api.include_router(observation_queries_router)
    api.include_router(observation_stream_router)

    @api.get("/health", response_model=LiveHealthResponse)
    async def health() -> LiveHealthResponse:
        return LiveHealthResponse(status="ok", service="correlis-api", version=__version__)

    @api.get("/health/live", response_model=LiveHealthResponse)
    async def health_live() -> LiveHealthResponse:
        return LiveHealthResponse(status="ok", service="correlis-api", version=__version__)

    @api.get("/health/ready", response_model=ReadinessResponse)
    async def health_ready(response: Response) -> ReadinessResponse:
        try:
            validate_credential_pepper(resolved_settings.credential_pepper)
            collector_auth_check = CollectorAuthHealthResponse(status="ok")
        except CredentialPepperConfigurationError:
            collector_auth_check = CollectorAuthHealthResponse(
                status="error", code="credential_pepper_not_configured"
            )
        database_engine = api.state.database_engine
        if database_engine is None:
            response.status_code = 503
            return ReadinessResponse(
                status="not_ready",
                service="correlis-api",
                version=__version__,
                checks=ReadinessChecks(
                    database=DatabaseHealthResponse(status="error", code="database_not_configured"),
                    migrations=MigrationHealthResponse(status="not_checked"),
                    collector_auth=collector_auth_check,
                    observation_sequence=ObservationSequenceHealthResponse(status="not_checked"),
                ),
            )

        database_check = check_database_connectivity(database_engine)
        if not database_check.ok:
            response.status_code = 503
            return ReadinessResponse(
                status="not_ready",
                service="correlis-api",
                version=__version__,
                checks=ReadinessChecks(
                    database=DatabaseHealthResponse(status="error", code=database_check.code),
                    migrations=MigrationHealthResponse(status="not_checked"),
                    collector_auth=collector_auth_check,
                    observation_sequence=ObservationSequenceHealthResponse(status="not_checked"),
                ),
            )

        migration_check = check_migration_state(
            database_engine, resolved_settings.alembic_config_path
        )
        migration_payload = MigrationHealthResponse(
            status="ok" if migration_check.ok else "error",
            code=None if migration_check.ok else migration_check.code,
            current=list(migration_check.current),
            expected=list(migration_check.expected),
        )
        if migration_check.ok:
            sequence_check = check_observation_sequence_state(database_engine)
            sequence_payload = ObservationSequenceHealthResponse(
                status=sequence_check.status,
                high_watermark=sequence_check.high_watermark,
                code=sequence_check.code,
            )
        else:
            sequence_check = None
            sequence_payload = ObservationSequenceHealthResponse(status="not_checked")
        ready = (
            migration_check.ok
            and collector_auth_check.status == "ok"
            and (sequence_check is not None and sequence_check.ok)
        )
        response.status_code = 200 if ready else 503
        return ReadinessResponse(
            status="ready" if ready else "not_ready",
            service="correlis-api",
            version=__version__,
            checks=ReadinessChecks(
                database=DatabaseHealthResponse(status="ok"),
                migrations=migration_payload,
                collector_auth=collector_auth_check,
                observation_sequence=sequence_payload,
            ),
        )

    @api.get("/api/v1/collectors/me", response_model=CollectorMeResponse)
    async def get_collector_me(
        principal: Annotated[AuthenticatedCollectorPrincipal, Depends(get_authenticated_collector)],
    ) -> CollectorMeResponse:
        return CollectorMeResponse(
            tenant_id=principal.tenant_id,
            collector_id=principal.collector_id,
            name=principal.collector_name,
            source=principal.source,
            credential_id=principal.credential_id,
        )

    @api.get("/api/v1/ontology", response_model=OntologyManifest)
    async def get_ontology(
        registry: Annotated[OntologyRegistry, Depends(get_ontology_registry)],
    ) -> OntologyManifest:
        return registry.manifest()

    @api.get("/api/v1/scenarios")
    async def list_scenarios(
        repository: Annotated[ScenarioRepository, Depends(get_scenario_repository)],
    ) -> dict[str, list[str]]:
        return {"scenarios": repository.list()}

    @api.get("/api/v1/scenarios/{name}/scene")
    async def get_scene(
        name: str,
        repository: Annotated[ScenarioRepository, Depends(get_scenario_repository)],
    ):
        try:
            observations = repository.load(name)
        except ScenarioNotFoundError as exc:
            raise HTTPException(status_code=404, detail="scenario not found") from exc
        return build_scene(name, observations, ontology_registry=api.state.ontology_registry)

    @api.websocket("/ws/scenarios/{name}/replay")
    async def replay_scenario(
        websocket: WebSocket,
        name: str,
        speed: float = Query(default=1.0, ge=0.0, le=100.0),
    ) -> None:
        await websocket.accept()
        repository: ScenarioRepository = websocket.app.state.scenario_repository
        try:
            observations = repository.load(name)
        except ScenarioNotFoundError:
            await websocket.send_json({"type": "error", "detail": "scenario not found"})
            await websocket.close(code=4404)
            return

        if not observations:
            await websocket.send_json({"type": "error", "detail": "scenario is empty"})
            await websocket.close(code=4400)
            return

        builder = SceneBuilder(
            scene_id=f"scene:{name}",
            tenant_id=observations[0].tenant_id,
            title=name.replace("-", " ").title(),
            ontology_registry=websocket.app.state.ontology_registry,
        )
        previous_time: datetime | None = None

        try:
            await websocket.send_json(
                {
                    "type": "replay_started",
                    "scene_id": builder.scene.id,
                    "observation_count": len(observations),
                }
            )
            for observation in observations:
                if previous_time is not None and speed > 0:
                    delay = max(
                        0.0,
                        (observation.event_time - previous_time).total_seconds() / speed,
                    )
                    await asyncio.sleep(min(delay, 5.0))
                delta = builder.apply(observation)
                await websocket.send_json(
                    {"type": "scene_delta", "data": delta.model_dump(mode="json")}
                )
                previous_time = observation.event_time

            await websocket.send_json(
                {"type": "replay_complete", "data": builder.scene.model_dump(mode="json")}
            )
        except WebSocketDisconnect:
            return

    return api


app = create_app()
