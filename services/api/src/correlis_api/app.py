from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from . import __version__
from .scenarios import ScenarioNotFoundError, ScenarioRepository
from .scene import SceneBuilder, build_scene
from .settings import settings

app = FastAPI(
    title="Correlis API",
    version=__version__,
    description="Reference attack-scene contract and replay service.",
)
repo = ScenarioRepository(settings.scenario_dir)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "correlis-api", "version": __version__}


@app.get("/api/v1/scenarios")
async def list_scenarios() -> dict[str, list[str]]:
    return {"scenarios": repo.list()}


@app.get("/api/v1/scenarios/{name}/scene")
async def get_scene(name: str):
    try:
        observations = repo.load(name)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404, detail="scenario not found") from exc
    return build_scene(name, observations)


@app.websocket("/ws/scenarios/{name}/replay")
async def replay_scenario(
    websocket: WebSocket,
    name: str,
    speed: float = Query(default=1.0, ge=0.0, le=100.0),
) -> None:
    await websocket.accept()
    try:
        observations = repo.load(name)
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
                delay = max(0.0, (observation.event_time - previous_time).total_seconds() / speed)
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
