from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from factorypilot.a2a.client import VendorExpertClient
from factorypilot.a2a.vendor_server import app as vendor_app
from factorypilot.config import Settings
from factorypilot.coordination import RedisCoordinator
from factorypilot.database import Database
from factorypilot.neo4j_knowledge import Neo4jFaultKnowledgeTools
from factorypilot.task_queue import TaskDispatcher, celery_app


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    def ping(self) -> bool:
        return True

    def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script: str, _key_count: int, key: str, owner: str) -> int:
        if self.values.get(key) != owner:
            return 0
        del self.values[key]
        return 1

    def publish(self, channel: str, value: str) -> int:
        self.published.append((channel, value))
        return 1

    def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.values[key] = value
        return True

    def close(self) -> None:
        return None


def test_redis_coordinator_uses_owner_checked_distributed_lock() -> None:
    coordinator = RedisCoordinator(Settings(redis_enabled=False))
    coordinator._client = FakeRedis()  # type: ignore[assignment]

    first = coordinator.acquire_run_lock("RUN-REDIS")
    second = coordinator.acquire_run_lock("RUN-REDIS")
    assert first is not None
    assert second is None

    coordinator.release_run_lock(first)
    third = coordinator.acquire_run_lock("RUN-REDIS")
    assert third is not None

    coordinator.publish_run_signal({"run_id": "RUN-REDIS", "event": "updated"})
    assert coordinator._client.published[0][0] == "factorypilot:events:RUN-REDIS"  # type: ignore[union-attr]


def test_celery_dispatcher_uses_deterministic_task_id(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_send_task(name: str, **kwargs: Any) -> None:
        calls.append({"name": name, **kwargs})

    monkeypatch.setattr(celery_app, "send_task", fake_send_task)
    settings = Settings(task_queue_backend="celery", celery_queue="diagnosis")
    dispatcher = TaskDispatcher(settings)

    task_id = dispatcher.enqueue_start("RUN-QUEUE", None, None)
    resume_id = dispatcher.enqueue_resume(
        "RUN-QUEUE",
        {"approval_id": "APR-001", "approved": True},
        None,
        None,
    )

    assert task_id == "run-start-RUN-QUEUE"
    assert resume_id == "run-resume-RUN-QUEUE-APR-001"
    assert calls[0] == {
        "name": "factorypilot.run.start",
        "args": ["RUN-QUEUE"],
        "queue": "diagnosis",
        "task_id": "run-start-RUN-QUEUE",
    }
    assert calls[1]["name"] == "factorypilot.run.resume"
    assert calls[1]["task_id"] == "run-resume-RUN-QUEUE-APR-001"


def test_vendor_a2a_agent_card_and_idempotent_json_rpc() -> None:
    client = TestClient(vendor_app)
    card = client.get("/.well-known/agent-card.json").json()
    assert card["protocolVersion"] == "0.3.0"
    assert card["preferredTransport"] == "JSONRPC"
    assert card["skills"][0]["id"] == "lm5000-fault-diagnosis"

    request = {
        "jsonrpc": "2.0",
        "id": "RPC-001",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": "MSG-IDEMPOTENT-001",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "query": "激光打码良率下降并出现模糊",
                            "model": "LM-5000",
                            "alarm_codes": ["OPT-207"],
                        },
                    }
                ],
            }
        },
    }
    headers = {"Authorization": "Bearer factorypilot-vendor-token"}
    first = client.post("/a2a", json=request, headers=headers)
    replay = client.post("/a2a", json=request, headers=headers)

    assert first.status_code == 200
    assert first.json()["result"]["status"]["state"] == "completed"
    assert replay.json()["result"]["id"] == first.json()["result"]["id"]
    diagnosis = first.json()["result"]["artifacts"][0]["parts"][0]["data"]
    assert diagnosis["likely_causes"][0]["manufacturer_code"] == "LM5K-OPTICS-01"


def test_a2a_client_discovers_card_and_writes_audit(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "a2a.db")
    database.initialize()
    database.create_run("RUN-A2A", "激光打码良率下降", "LINE-02")
    settings = Settings(a2a_enabled=True, a2a_fail_open=False)
    client = VendorExpertClient(database, settings)

    responses = iter(
        [
            {
                "protocolVersion": "0.3.0",
                "preferredTransport": "JSONRPC",
                "url": "http://localhost:8200/a2a",
                "name": "LM-5000 Equipment Vendor Expert",
            },
            {
                "jsonrpc": "2.0",
                "id": "RPC-1",
                "result": {
                    "id": "TASK-1",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "parts": [
                                {
                                    "kind": "data",
                                    "data": {"likely_causes": [{"cause": "镜片污染"}]},
                                }
                            ]
                        }
                    ],
                },
            },
        ]
    )
    monkeypatch.setattr(client, "_request_json", lambda *_args, **_kwargs: next(responses))

    result = client.consult("RUN-A2A", {"model": "LM-5000", "query": "良率下降"})
    assert result is not None
    assert result["task_id"] == "TASK-1"
    audit = database.fetch_one(
        "SELECT tool, operation, outcome FROM audit_logs WHERE run_id=?",
        ("RUN-A2A",),
    )
    assert audit == {
        "tool": "vendor-a2a",
        "operation": "message/send",
        "outcome": "success",
    }


class FakeNeo4jRecord:
    def data(self) -> dict[str, Any]:
        return {
            "path_id": "FM-LM5000-001",
            "component": "保护镜片",
            "failure": "镜片污染",
            "risk": "high",
            "symptom": "良率下降",
            "remedy": "停机清洁保护镜片",
            "sop_id": "SOP-LASER-007",
            "section": "3.2",
            "matches": 2,
        }


class FakeNeo4jDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify_connectivity(self) -> None:
        return None

    def execute_query(self, query: str, **kwargs: Any):
        self.calls.append((query, kwargs))
        if "RETURN failure.code AS path_id" in query:
            return [FakeNeo4jRecord()], None, None
        return [], None, None

    def close(self) -> None:
        return None


def test_neo4j_fault_traversal_is_parameterized_and_audited(tmp_path) -> None:
    database = Database(tmp_path / "neo4j.db")
    database.initialize()
    database.create_run("RUN-GRAPH", "激光打码良率下降", "LINE-02")
    driver = FakeNeo4jDriver()
    settings = Settings(neo4j_enabled=True, neo4j_fail_open=False)
    tools = Neo4jFaultKnowledgeTools(database, settings, driver=driver)

    paths = tools.search_fault_paths(
        "RUN-GRAPH", "LM-5000", "打码模糊，良率和能量下降", limit=4
    )

    assert paths[0]["failure"] == "镜片污染"
    search_query, parameters = driver.calls[-1]
    assert "$model" in search_query and "$keywords" in search_query and "$limit" in search_query
    assert parameters["model"] == "LM-5000"
    assert parameters["limit"] == 4
    assert "良率" in parameters["keywords"]
    audit = database.fetch_one(
        "SELECT tool, operation, outcome FROM audit_logs WHERE run_id=?",
        ("RUN-GRAPH",),
    )
    assert audit["tool"] == "neo4j-graph"
    assert audit["operation"] == "traverse_fault_paths"
    assert audit["outcome"] == "success:1"
