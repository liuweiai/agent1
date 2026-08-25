from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .database import Database


class ToolValidationError(ValueError):
    """Raised when a tool request violates the explicit parameter policy."""


@dataclass(frozen=True)
class ToolPolicy:
    timeout_seconds: float = 3.0
    max_attempts: int = 2


class MesReadTools:
    """Read-only MES facade.

    The graph selects a named operation and validated parameters. It never receives
    a generic SQL execution tool, which prevents arbitrary model-generated SQL.
    """

    LINE_PATTERN = re.compile(r"^LINE-\d{2}$")
    DEVICE_PATTERN = re.compile(r"^DEV-L\d{2}-LASER$")

    def __init__(self, database: Database, policy: ToolPolicy | None = None):
        self.database = database
        self.policy = policy or ToolPolicy()

    def _validate_line(self, line_id: str) -> None:
        if not self.LINE_PATTERN.fullmatch(line_id):
            raise ToolValidationError("line_id 不符合 LINE-XX 白名单格式")

    def _validate_device(self, device_id: str) -> None:
        if not self.DEVICE_PATTERN.fullmatch(device_id):
            raise ToolValidationError("device_id 不符合设备白名单格式")

    def get_line_device(self, run_id: str, line_id: str) -> dict[str, Any]:
        self._validate_line(line_id)
        return self._read_with_audit(
            run_id,
            "get_line_device",
            {"line_id": line_id},
            lambda: self.database.fetch_one(
                "SELECT * FROM devices WHERE line_id=?", (line_id,)
            ),
        )

    def get_recent_alerts(
        self, run_id: str, device_id: str, hours: int = 24, limit: int = 20
    ) -> list[dict[str, Any]]:
        self._validate_device(device_id)
        if hours not in {8, 24, 72, 168}:
            raise ToolValidationError("hours 只允许 8/24/72/168")
        if not 1 <= limit <= 50:
            raise ToolValidationError("limit 必须在 1..50")
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return self._read_with_audit(
            run_id,
            "get_recent_alerts",
            {"device_id": device_id, "hours": hours, "limit": limit},
            lambda: self.database.fetch_all(
                """
                SELECT id,code,level,message,occurred_at,acknowledged
                FROM alerts WHERE device_id=? AND occurred_at>=?
                ORDER BY occurred_at DESC LIMIT ?
                """,
                (device_id, since, limit),
            ),
        )

    def get_recent_quality(
        self, run_id: str, line_id: str, limit: int = 12
    ) -> list[dict[str, Any]]:
        self._validate_line(line_id)
        if not 3 <= limit <= 30:
            raise ToolValidationError("limit 必须在 3..30")
        return self._read_with_audit(
            run_id,
            "get_recent_quality",
            {"line_id": line_id, "limit": limit},
            lambda: self.database.fetch_all(
                """
                SELECT b.id AS batch_id,b.started_at,b.produced_qty,b.good_qty,
                       ROUND(100.0*b.good_qty/b.produced_qty,2) AS yield_rate,
                       b.marking_speed,b.material_lot,i.defect_type,i.defect_qty
                FROM batches b JOIN inspections i ON i.batch_id=b.id
                WHERE b.line_id=? ORDER BY b.started_at DESC LIMIT ?
                """,
                (line_id, limit),
            ),
        )

    def get_maintenance_history(
        self, run_id: str, device_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        self._validate_device(device_id)
        if not 1 <= limit <= 20:
            raise ToolValidationError("limit 必须在 1..20")
        return self._read_with_audit(
            run_id,
            "get_maintenance_history",
            {"device_id": device_id, "limit": limit},
            lambda: self.database.fetch_all(
                """
                SELECT id,category,description,resolution,completed_at
                FROM maintenance_records WHERE device_id=?
                ORDER BY completed_at DESC LIMIT ?
                """,
                (device_id, limit),
            ),
        )

    def _read_with_audit(
        self,
        run_id: str,
        operation: str,
        parameters: dict[str, Any],
        callback: Callable[[], Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            result = callback()
            outcome = f"success:{len(result) if isinstance(result, list) else int(result is not None)}"
            return result
        except Exception as exc:
            outcome = f"failed:{type(exc).__name__}"
            raise
        finally:
            self.database.audit(
                run_id,
                "DataAgent",
                "mes-mcp",
                operation,
                parameters,
                outcome,
                int((time.perf_counter() - started) * 1000),
            )


class KnowledgeTools:
    """MVP lexical/hybrid retrieval facade with paragraph-level source metadata."""

    def __init__(self, database: Database):
        self.database = database

    def search(self, run_id: str, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        if not 1 <= top_k <= 8:
            raise ToolValidationError("top_k 必须在 1..8")
        started = time.perf_counter()
        docs = self.database.fetch_all("SELECT * FROM knowledge_docs")
        keywords = self._keywords(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in docs:
            haystack = f"{doc['title']} {doc['section']} {doc['content']} {doc['tags']}".lower()
            matched = sum(1 for keyword in keywords if keyword in haystack)
            domain_boost = 2 if any(tag in doc["tags"] for tag in ("optics", "quality", "safety")) else 0
            score = min(0.99, 0.35 + matched * 0.09 + domain_boost * 0.05)
            if matched or domain_boost:
                scored.append((score, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        result = [
            {
                "id": doc["id"],
                "title": doc["title"],
                "section": doc["section"],
                "content": doc["content"],
                "version": doc["version"],
                "score": round(score, 3),
            }
            for score, doc in scored[:top_k]
        ]
        self.database.audit(
            run_id,
            "RCAAgent",
            "knowledge-mcp",
            "hybrid_search",
            {"query": query[:200], "top_k": top_k},
            f"success:{len(result)}",
            int((time.perf_counter() - started) * 1000),
        )
        return result

    @staticmethod
    def _keywords(query: str) -> set[str]:
        dictionary = {
            "良率",
            "打码",
            "激光",
            "模糊",
            "偏移",
            "功率",
            "能量",
            "镜片",
            "质量",
            "审批",
            "维护",
            "异常",
        }
        return {word for word in dictionary if word in query} | {"optics", "quality", "safety"}


class SafeActionTools:
    """Allowlisted simulated MES write tools. Every call requires approval context."""

    def __init__(self, database: Database):
        self.database = database

    def create_maintenance_order(
        self,
        run_id: str,
        device_id: str,
        priority: str,
        title: str,
        description: str,
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        if not approval.get("approved") or not approval.get("approver"):
            raise ToolValidationError("写操作缺少有效人工审批")
        if priority not in {"P1", "P2", "P3"}:
            raise ToolValidationError("工单优先级只允许 P1/P2/P3")
        if not MesReadTools.DEVICE_PATTERN.fullmatch(device_id):
            raise ToolValidationError("device_id 不在允许范围")
        if not 4 <= len(title) <= 80 or not 10 <= len(description) <= 500:
            raise ToolValidationError("工单标题或描述长度不符合策略")
        parameters = {
            "device_id": device_id,
            "priority": priority,
            "title": title,
            "approval_id": approval.get("approval_id"),
        }
        started = time.perf_counter()
        order_id = f"WO-{uuid.uuid4().hex[:8].upper()}"
        self.database.execute(
            "INSERT INTO work_orders VALUES (?,?,?,?,?,?,?,?)",
            (order_id, run_id, device_id, priority, title, description, "created", datetime.now(timezone.utc).isoformat()),
        )
        self.database.audit(
            run_id,
            approval["approver"],
            "action-mcp",
            "create_maintenance_order",
            parameters,
            "success",
            int((time.perf_counter() - started) * 1000),
        )
        return {"work_order_id": order_id, "status": "created", **parameters}

    def tighten_inspection(
        self,
        run_id: str,
        line_id: str,
        interval_minutes: int,
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        if not approval.get("approved") or not approval.get("approver"):
            raise ToolValidationError("写操作缺少有效人工审批")
        if not MesReadTools.LINE_PATTERN.fullmatch(line_id):
            raise ToolValidationError("line_id 不在允许范围")
        if interval_minutes not in {5, 10, 15, 30}:
            raise ToolValidationError("抽检周期只允许 5/10/15/30 分钟")
        before = self.database.fetch_one(
            "SELECT inspection_interval_minutes FROM line_controls WHERE line_id=?", (line_id,)
        )
        started = time.perf_counter()
        self.database.execute(
            "UPDATE line_controls SET inspection_interval_minutes=?,updated_at=? WHERE line_id=?",
            (interval_minutes, datetime.now(timezone.utc).isoformat(), line_id),
        )
        parameters = {
            "line_id": line_id,
            "before": before["inspection_interval_minutes"] if before else None,
            "after": interval_minutes,
            "approval_id": approval.get("approval_id"),
        }
        self.database.audit(
            run_id,
            approval["approver"],
            "action-mcp",
            "tighten_inspection",
            parameters,
            "success",
            int((time.perf_counter() - started) * 1000),
        )
        return {"status": "updated", **parameters}
