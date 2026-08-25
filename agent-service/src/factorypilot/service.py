from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .analysis_engine import DemoRcaEngine, OpenAIRcaEngine
from .config import Settings
from .graph import FactoryPilotGraph
from .mcp_client_tools import create_remote_tools
from .milvus_knowledge import create_knowledge_tools
from .models import ApprovalRequest, RunCreate
from .mysql_database import create_database
from .tools import KnowledgeTools, MesReadTools, SafeActionTools


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "value"):
        return value.value
    return str(value)


class AgentService:
    """Owns the durable graph runtime and guards per-run execution concurrency."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_directories()
        self.database = create_database(settings)
        self.database.initialize()
        self._checkpoint_connection = sqlite3.connect(
            str(settings.checkpoint_path), check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        rca_engine = (
            OpenAIRcaEngine(
                settings.openai_model,
                settings.openai_api_key,
                settings.openai_base_url,
                settings.llm_fail_open,
                settings.llm_timeout_seconds,
                settings.llm_max_retries,
            )
            if settings.agent_mode == "llm"
            else DemoRcaEngine()
        )
        if settings.tool_transport == "mcp":
            mes_tools, knowledge_tools, action_tools = create_remote_tools(settings)
        else:
            mes_tools = MesReadTools(self.database)
            knowledge_tools = create_knowledge_tools(self.database, settings)
            action_tools = SafeActionTools(self.database)
        factory = FactoryPilotGraph(
            self.database, mes_tools, knowledge_tools, action_tools, rca_engine
        )
        self.graph = factory.build(self.checkpointer)
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def close(self) -> None:
        self._checkpoint_connection.close()

    @staticmethod
    def config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 30}

    def create_run(self, request: RunCreate) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        self.database.create_run(run_id, request.query, request.line_id)
        self.database.emit_event(
            run_id,
            "request",
            "接收异常请求",
            "completed",
            "已创建持久化诊断任务，等待工作流调度",
            {"query": request.query, "line_id": request.line_id},
        )
        return self.database.get_run(run_id) or {}

    def start_run(self, run_id: str) -> None:
        if not self._claim(run_id):
            return
        try:
            run = self.database.get_run(run_id)
            if not run:
                return
            initial = {
                "run_id": run_id,
                "query": run["query"],
                "line_id": run["line_id"],
                "evidence": [],
            }
            self.graph.invoke(initial, config=self.config(run_id))
            self._save_graph_state(run_id)
        except Exception as exc:
            self._mark_failed(run_id, exc)
        finally:
            self._release(run_id)

    def approve(self, run_id: str, request: ApprovalRequest) -> dict[str, Any]:
        run = self.database.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run["status"] != "awaiting_approval":
            raise RuntimeError(f"当前状态 {run['status']} 不允许审批")
        approval = {
            "approval_id": f"APR-{uuid.uuid4().hex[:10].upper()}",
            "approved": request.approved,
            "approver": request.approver,
            "comment": request.comment,
        }
        # Reserve the run before the background task is scheduled to prevent double approval.
        self.database.update_run(run_id, status="executing" if request.approved else "running")
        return approval

    def resume_run(self, run_id: str, approval: dict[str, Any]) -> None:
        if not self._claim(run_id):
            return
        try:
            self.database.emit_event(
                run_id,
                "approval_decision",
                "人工审批决定",
                "completed",
                f"{approval['approver']} 已{'批准' if approval['approved'] else '拒绝'}处置方案",
                approval,
            )
            self.database.audit(
                run_id,
                approval["approver"],
                "approval-gate",
                "approve_action_plan",
                {"approved": approval["approved"], "comment": approval["comment"]},
                "approved" if approval["approved"] else "rejected",
                0,
            )
            self.graph.invoke(Command(resume=approval), config=self.config(run_id))
            self._save_graph_state(run_id)
            # Publish the terminal status only after the final graph state is durable.
            # SSE clients can therefore never observe a completed run with stale state.
            self.database.update_run(
                run_id, status="completed" if approval["approved"] else "rejected"
            )
        except Exception as exc:
            self._mark_failed(run_id, exc)
        finally:
            self._release(run_id)

    def _save_graph_state(self, run_id: str) -> None:
        snapshot = self.graph.get_state(self.config(run_id))
        serialized = json.dumps(snapshot.values, ensure_ascii=False, default=_json_default)
        self.database.update_run(run_id, state_json=serialized)

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        self.database.update_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        self.database.emit_event(
            run_id,
            "error",
            "工作流执行失败",
            "failed",
            f"{type(exc).__name__}: {exc}",
            idempotency_key=f"error:{type(exc).__name__}",
        )

    def _claim(self, run_id: str) -> bool:
        with self._lock:
            if run_id in self._running:
                return False
            self._running.add(run_id)
            return True

    def _release(self, run_id: str) -> None:
        with self._lock:
            self._running.discard(run_id)
