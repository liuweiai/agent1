from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import get_settings
from .models import ApprovalRequest, RunCreate, RunDetail, RunSummary
from .service import AgentService


TERMINAL_STATUSES = {"completed", "rejected", "failed"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = AgentService(get_settings())
    app.state.agent_service = service
    yield
    service.close()


app = FastAPI(
    title="FactoryPilot Agent Service",
    version="0.1.0",
    description="MES 生产异常诊断与闭环处置 Agent（模拟数据 MVP）",
    lifespan=lifespan,
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def verify_gateway(request: Request, call_next):
    """Prevent direct API bypass when the production gateway profile is enabled."""
    if settings.require_gateway_auth and request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-gateway-secret", "")
        if not hmac.compare_digest(supplied, settings.gateway_shared_secret):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=401, content={"detail": "请求必须经过受信任网关"})
    return await call_next(request)


def get_service(request: Request) -> AgentService:
    return request.app.state.agent_service


@app.get("/health")
def health(request: Request) -> dict[str, str]:
    service = get_service(request)
    service.database.fetch_one("SELECT 1 AS ok")
    return {
        "status": "ok",
        "profile": settings.agent_mode,
        "data": settings.data_backend,
        "knowledge": settings.knowledge_backend,
        "tools": settings.tool_transport,
    }


@app.post("/api/runs", response_model=RunSummary, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: RunCreate, background_tasks: BackgroundTasks, request: Request
) -> dict:
    service = get_service(request)
    run = service.create_run(payload)
    background_tasks.add_task(service.start_run, run["id"])
    return run


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs(request: Request, limit: int = Query(default=30, ge=1, le=100)) -> list[dict]:
    return get_service(request).database.list_runs(limit)


@app.get("/api/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, request: Request) -> dict:
    run = get_service(request).database.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="任务不存在")
    return run


@app.post(
    "/api/runs/{run_id}/approval",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_run(
    run_id: str,
    payload: ApprovalRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict:
    service = get_service(request)
    try:
        approval = service.approve(run_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(service.resume_run, run_id, approval)
    return service.database.get_run(run_id) or {}


@app.get("/api/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    service = get_service(request)
    if not service.database.get_run(run_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    async def generator() -> AsyncIterator[str]:
        cursor = after
        idle_ticks = 0
        while not await request.is_disconnected():
            events = service.database.get_events(run_id, cursor)
            if events:
                idle_ticks = 0
                for event in events:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: timeline\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            else:
                idle_ticks += 1
            run = service.database.get_run(run_id)
            if run and run["status"] in TERMINAL_STATUSES and not events:
                payload = {"run_id": run_id, "status": run["status"]}
                yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            if idle_ticks >= 20:
                yield ": heartbeat\n\n"
                idle_ticks = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
