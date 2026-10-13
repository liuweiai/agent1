from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from celery import Celery, Task

from .config import Settings, get_settings


def create_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    broker = settings.celery_broker_url or settings.redis_url
    backend = settings.celery_result_backend or broker
    application = Celery("factorypilot", broker=broker, backend=backend)
    application.conf.update(
        task_default_queue=settings.celery_queue,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        result_expires=3600,
        task_track_started=True,
        task_time_limit=settings.celery_task_timeout_seconds,
        task_soft_time_limit=max(30, settings.celery_task_timeout_seconds - 15),
    )
    return application


celery_app = create_celery_app()


@lru_cache(maxsize=1)
def get_worker_service():
    # Imported lazily so every Celery worker process owns its DB/checkpoint clients.
    from .service import AgentService

    return AgentService(get_settings())


class DurableRunTask(Task):
    max_retries = 3
    acks_late = True
    reject_on_worker_lost = True


@celery_app.task(base=DurableRunTask, bind=True, name="factorypilot.run.start")
def start_run_task(self, run_id: str) -> dict[str, Any]:
    service = get_worker_service()
    try:
        service.start_run(run_id, propagate_transient=True)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            service.mark_queue_failed(run_id, exc)
            raise
        raise self.retry(
            exc=exc, countdown=min(30, 2 ** (self.request.retries + 1))
        ) from exc
    run = service.database.get_run(run_id) or {}
    return {"run_id": run_id, "status": run.get("status", "unknown")}


@celery_app.task(base=DurableRunTask, bind=True, name="factorypilot.run.resume")
def resume_run_task(self, run_id: str, approval_json: str) -> dict[str, Any]:
    service = get_worker_service()
    approval = json.loads(approval_json)
    try:
        service.resume_run(run_id, approval, propagate_transient=True)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            service.mark_queue_failed(run_id, exc)
            raise
        raise self.retry(
            exc=exc, countdown=min(30, 2 ** (self.request.retries + 1))
        ) from exc
    run = service.database.get_run(run_id) or {}
    return {"run_id": run_id, "status": run.get("status", "unknown")}


class TaskDispatcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.distributed = settings.task_queue_backend == "celery"

    def enqueue_start(self, run_id: str, background_tasks, local_service) -> str:
        task_id = f"run-start-{run_id}"
        if self.distributed:
            celery_app.send_task(
                "factorypilot.run.start",
                args=[run_id],
                queue=self.settings.celery_queue,
                task_id=task_id,
            )
        else:
            background_tasks.add_task(local_service.start_run, run_id)
        return task_id

    def enqueue_resume(
        self, run_id: str, approval: dict[str, Any], background_tasks, local_service
    ) -> str:
        approval_id = str(approval.get("approval_id", "unknown"))
        task_id = f"run-resume-{run_id}-{approval_id}"
        if self.distributed:
            celery_app.send_task(
                "factorypilot.run.resume",
                args=[run_id, json.dumps(approval, ensure_ascii=False)],
                queue=self.settings.celery_queue,
                task_id=task_id,
            )
        else:
            background_tasks.add_task(local_service.resume_run, run_id, approval)
        return task_id


def worker_main() -> None:
    celery_app.worker_main(
        ["worker", "--loglevel=INFO", "--queues", get_settings().celery_queue]
    )
