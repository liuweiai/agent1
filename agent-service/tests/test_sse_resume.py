import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from factorypilot import main
from factorypilot.database import Database


@pytest.fixture
def stream_client(tmp_path, monkeypatch):
    database = Database(tmp_path / "events.db")
    database.initialize()
    database.create_run("RUN-SSE", "生产异常测试", "LINE-02")
    database.update_run("RUN-SSE", status="completed")
    for number in range(1, 4):
        database.emit_event("RUN-SSE", f"step-{number}", "测试步骤", "completed", "已完成")
    service = SimpleNamespace(database=database, coordinator=SimpleNamespace(subscribe=lambda _: None))
    monkeypatch.setattr(main.app.state, "agent_service", service, raising=False)
    monkeypatch.setattr(main.settings, "require_gateway_auth", False)
    client = TestClient(main.app)
    yield client, database
    client.close()


def timeline_ids(response):
    assert response.status_code == 200
    return [json.loads(line[6:])["id"] for line in response.text.splitlines()
            if line.startswith("data: ") and '"id":' in line]


def test_reconnect_replays_only_events_after_last_received_id(stream_client):
    client, database = stream_client
    first = client.get("/api/runs/RUN-SSE/events")
    ids = timeline_ids(first)
    assert len(ids) == 3
    # A new persisted event appears while the browser is disconnected.
    database.emit_event("RUN-SSE", "report", "报告生成", "completed", "报告已保存")
    new_id = database.get_events("RUN-SSE")[-1]["id"]
    replay = client.get(f"/api/runs/RUN-SSE/events?after={ids[0]}", headers={"Last-Event-ID": str(ids[-1])})
    assert timeline_ids(replay) == [new_id]
    assert "event: done" in replay.text
    assert timeline_ids(client.get(f"/api/runs/RUN-SSE/events?after={new_id}")) == []


def test_query_cursor_can_be_newer_than_header(stream_client):
    client, database = stream_client
    ids = [event["id"] for event in database.get_events("RUN-SSE")]
    response = client.get(f"/api/runs/RUN-SSE/events?after={ids[1]}", headers={"Last-Event-ID": str(ids[0])})
    assert timeline_ids(response) == [ids[2]]


@pytest.mark.parametrize("value", ["-1", "1.5", "invalid", "9007199254740992", "١"])
def test_invalid_last_event_id_is_rejected(stream_client, value):
    client, _ = stream_client
    # Non-ASCII header bytes are tested directly because HTTPX validates header encoding.
    if not value.isascii():
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            main.resolve_event_cursor(0, value)
    else:
        assert client.get("/api/runs/RUN-SSE/events", headers={"Last-Event-ID": value}).status_code == 400


def test_invalid_query_cursor_and_unknown_run(stream_client):
    client, _ = stream_client
    assert client.get("/api/runs/RUN-SSE/events?after=-1").status_code == 422
    assert client.get("/api/runs/RUN-SSE/events?after=9007199254740992").status_code == 422
    assert client.get("/api/runs/unknown/events").status_code == 404
