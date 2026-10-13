from pathlib import Path

import pytest

from factorypilot.config import Settings
from factorypilot.models import ApprovalRequest, RunCreate
from factorypilot.service import AgentService
from factorypilot.tools import SafeActionTools


@pytest.fixture
def service(tmp_path: Path):
    instance = AgentService(Settings(
        _env_file=None, database_path=tmp_path / "mes.db", checkpoint_path=tmp_path / "graph.db",
        data_backend="sqlite", agent_mode="demo", knowledge_backend="lexical", tool_transport="inprocess",
        redis_enabled=False, neo4j_enabled=False, a2a_enabled=False, task_queue_backend="local",
    ))
    yield instance
    instance.close()


def pending_run(service):
    run = service.create_run(RunCreate(query="2号产线激光打码良率下降到91%"))
    service.start_run(run["id"])
    approval = service.approve(run["id"], ApprovalRequest(approved=True, approver="supervisor"))
    return run["id"], approval


@pytest.mark.parametrize("fault", ["missing_order", "wrong_device", "missing_control", "wrong_interval"])
def test_silent_write_failure_has_failed_report_and_no_automatic_reexecution(service, monkeypatch, fault):
    run_id, approval = pending_run(service)
    calls = []
    original_order = SafeActionTools.create_maintenance_order
    original_inspection = SafeActionTools.tighten_inspection

    def create_order(self, run_id, device_id, *args):
        calls.append("order")
        if fault == "missing_order":
            return {"work_order_id": "WO-NOT-PERSISTED", "status": "created"}
        return original_order(self, run_id, "DEV-L01-LASER" if fault == "wrong_device" else device_id, *args)

    def inspection(self, run_id, line_id, *args):
        calls.append("inspection")
        result = original_inspection(self, run_id, line_id, *args)
        if fault == "missing_control":
            self.database.execute("DELETE FROM line_controls WHERE line_id=?", (line_id,))
        if fault == "wrong_interval":
            self.database.execute("UPDATE line_controls SET inspection_interval_minutes=30 WHERE line_id=?", (line_id,))
        return result

    monkeypatch.setattr(SafeActionTools, "create_maintenance_order", create_order)
    monkeypatch.setattr(SafeActionTools, "tighten_inspection", inspection)
    service.resume_run(run_id, approval, propagate_transient=True)
    result = service.database.get_run(run_id)
    assert result["status"] == "failed"
    assert result["report"]["outcome"] == "failed"
    verification = result["state"]["verification"]
    assert verification["write_state_verified"] is False
    assert verification["manual_intervention_required"] is True
    assert verification["release_condition_met"] is False
    assert verification["verification_batches"] == []
    assert verification["failure_reasons"]
    assert result["error"] == verification["note"]
    assert result["state"]["production_analysis"]["recovery_validation"]["status"] == "pending"
    assert any(event["step"] == "verification_failed" for event in result["events"])
    assert any(event["step"] == "report" for event in result["events"])
    assert not service.graph.get_state(service.config(run_id)).next
    assert calls == ["order", "inspection"]


def test_successful_writes_and_observing_quality_are_distinct(service):
    run_id, approval = pending_run(service)
    service.resume_run(run_id, approval)
    result = service.database.get_run(run_id)
    assert result["status"] == result["report"]["outcome"] == "completed"
    assert result["state"]["verification"]["write_state_verified"] is True
    assert result["state"]["verification"]["release_condition_met"] is False
    assert "verification_failed" not in [event["step"] for event in result["events"]]


def test_finalization_retry_preserves_failed_outcome_without_repeating_writes(service, monkeypatch):
    run_id, approval = pending_run(service)
    calls = []

    def missing_order(*_args):
        calls.append("write")
        return {"work_order_id": "WO-ABSENT"}

    monkeypatch.setattr(SafeActionTools, "create_maintenance_order", missing_order)
    original = service.database.update_run
    failed_once = False

    def fail_terminal_update(run_id, **fields):
        nonlocal failed_once
        if fields.get("status") == "failed" and not failed_once:
            failed_once = True
            raise ConnectionError("temporary finalization failure")
        return original(run_id, **fields)

    monkeypatch.setattr(service.database, "update_run", fail_terminal_update)
    with pytest.raises(ConnectionError):
        service.resume_run(run_id, approval, propagate_transient=True)
    assert service.database.get_run(run_id)["status"] == "executing"
    service.resume_run(run_id, approval, propagate_transient=True)
    result = service.database.get_run(run_id)
    assert result["status"] == result["report"]["outcome"] == "failed"
    assert calls == ["write"]
