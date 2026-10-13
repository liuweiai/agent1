from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from factorypilot.config import Settings, get_settings
from factorypilot.main import app
from factorypilot.service import AgentService


def wait_for_status(service: AgentService, run_id: str, statuses: set[str]) -> dict:
    for _ in range(100):
        run = service.database.get_run(run_id)
        if run and run["status"] in statuses:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach {statuses}")


def test_approval_executes_allowlisted_actions(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "mes.db",
        checkpoint_path=tmp_path / "checkpoints.db",
        agent_mode="demo",
    )
    service = AgentService(settings)
    try:
        from factorypilot.models import ApprovalRequest, RunCreate

        run = service.create_run(
            RunCreate(query="2号产线激光打码设备良率从 98% 降到 91%，分析原因", line_id="LINE-02")
        )
        service.start_run(run["id"])
        pending = wait_for_status(service, run["id"], {"awaiting_approval"})
        assert pending["risk_level"] == "high"
        assert pending["state"]["hypotheses"][0]["confidence"] >= 0.8
        analysis = pending["state"]["production_analysis"]
        assert analysis["window_size"] == 20
        assert analysis["baseline"]["average_yield"] > 97
        assert analysis["anomaly"]["average_yield"] < 95
        assert analysis["alarm_overlap"]["overlap"] is True
        assert {"STAT-WINDOW-1", "STAT-CORR-1", "STAT-TIME-1"}.issubset(
            {item["id"] for item in pending["state"]["evidence"]}
        )

        approval = service.approve(
            run["id"], ApprovalRequest(approved=True, approver="测试主管", comment="批准模拟处置")
        )
        service.resume_run(run["id"], approval)
        completed = wait_for_status(service, run["id"], {"completed"})
        assert completed["report"]["data_notice"].startswith("本报告")
        assert len(completed["state"]["action_results"]) == 2
        assert completed["state"]["production_analysis"]["recovery_validation"][
            "current_consecutive_batches"
        ] == 2
        writes = service.database.fetch_all(
            "SELECT operation FROM audit_logs WHERE run_id=? AND tool='action-mcp'", (run["id"],)
        )
        assert {row["operation"] for row in writes} == {
            "create_maintenance_order",
            "tighten_inspection",
        }
    finally:
        service.close()


def test_rejection_produces_no_action_writes(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "mes.db",
        checkpoint_path=tmp_path / "checkpoints.db",
        agent_mode="demo",
    )
    service = AgentService(settings)
    try:
        from factorypilot.models import ApprovalRequest, RunCreate

        run = service.create_run(RunCreate(query="2号产线激光打码良率降到 91%", line_id="LINE-02"))
        service.start_run(run["id"])
        approval = service.approve(
            run["id"], ApprovalRequest(approved=False, approver="测试主管", comment="先线下确认")
        )
        service.resume_run(run["id"], approval)
        rejected = wait_for_status(service, run["id"], {"rejected"})
        assert rejected["state"]["action_results"] == []
        count = service.database.fetch_one(
            "SELECT COUNT(*) AS count FROM audit_logs WHERE run_id=? AND tool='action-mcp'",
            (run["id"],),
        )
        assert count["count"] == 0
    finally:
        service.close()


def test_health_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "demo")
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "lexical")
    monkeypatch.setenv("TOOL_TRANSPORT", "inprocess")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["data"] == "sqlite"
            assert response.json()["knowledge"] == "lexical"
            assert response.json()["tools"] == "inprocess"
    finally:
        get_settings.cache_clear()


def test_audit_log_repository_returns_parsed_parameters(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "mes.db",
        checkpoint_path=tmp_path / "checkpoints.db",
        agent_mode="demo",
    )
    service = AgentService(settings)
    try:
        service.database.audit(
            run_id="run-audit-test",
            actor="DataAgent",
            tool="mes-mcp",
            operation="get_recent_quality",
            parameters={"line_id": "LINE-02", "limit": 20},
            outcome="success",
            duration_ms=18,
        )

        rows = service.database.list_audit_logs(limit=10, run_id="run-audit-test")

        assert len(rows) == 1
        assert rows[0]["parameters"] == {"line_id": "LINE-02", "limit": 20}
        assert "parameters_json" not in rows[0]
    finally:
        service.close()
