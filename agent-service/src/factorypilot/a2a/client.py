from __future__ import annotations

import json
import time
import uuid
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .types import A2AProtocolError
from ..config import Settings
from ..database import Database


class DisabledVendorExpertClient:
    enabled = False

    def consult(self, run_id: str, context: dict[str, Any]) -> dict[str, Any] | None:
        return None

    def health(self) -> bool:
        return False


class VendorExpertClient:
    """Small A2A v0.3 JSON-RPC client with Agent Card discovery."""

    enabled = True

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self.card: dict[str, Any] | None = None

    def _request_json(
        self, url: str, *, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "A2A-Version": "0.3.0"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            token = self.settings.vendor_a2a_token.get_secret_value()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
        with urlopen(request, timeout=self.settings.a2a_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def discover(self) -> dict[str, Any]:
        if self.card:
            return self.card
        card = self._request_json(self.settings.vendor_a2a_card_url)
        if card.get("protocolVersion") != "0.3.0":
            raise A2AProtocolError("厂商 Agent Card 协议版本不是 0.3.0")
        if card.get("preferredTransport") != "JSONRPC":
            raise A2AProtocolError("厂商 Agent 未声明 JSONRPC transport")
        endpoint = str(card.get("url", ""))
        card_host = urlparse(self.settings.vendor_a2a_card_url).hostname
        endpoint_host = urlparse(endpoint).hostname
        if urlparse(endpoint).scheme not in {"http", "https"} or endpoint_host != card_host:
            raise A2AProtocolError("Agent Card 返回了不受信任的 A2A endpoint")
        self.card = card
        return card

    def consult(self, run_id: str, context: dict[str, Any]) -> dict[str, Any] | None:
        started = time.perf_counter()
        message_id = f"msg-{run_id}-vendor"
        try:
            card = self.discover()
            payload = {
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "data", "data": context}],
                        "messageId": message_id,
                    },
                    "metadata": {"caller": "factorypilot", "runId": run_id},
                },
            }
            response = self._request_json(str(card["url"]), payload=payload)
            if response.get("error"):
                raise A2AProtocolError(str(response["error"]))
            task = response.get("result", {})
            if task.get("status", {}).get("state") != "completed":
                raise A2AProtocolError("厂商专家任务未完成")
            for artifact in task.get("artifacts", []):
                for part in artifact.get("parts", []):
                    if part.get("kind") == "data" and isinstance(part.get("data"), dict):
                        result = {
                            **part["data"],
                            "agent_name": card.get("name"),
                            "task_id": task.get("id"),
                            "agent_card": self.settings.vendor_a2a_card_url,
                        }
                        self.database.audit(
                            run_id,
                            "RCAAgent",
                            "vendor-a2a",
                            "message/send",
                            {"message_id": message_id, "model": context.get("model")},
                            "success",
                            int((time.perf_counter() - started) * 1000),
                        )
                        return result
            raise A2AProtocolError("厂商专家响应缺少 data artifact")
        except Exception as exc:
            self.database.audit(
                run_id,
                "RCAAgent",
                "vendor-a2a",
                "message/send",
                {"message_id": message_id, "model": context.get("model")},
                f"failed:{type(exc).__name__}",
                int((time.perf_counter() - started) * 1000),
            )
            if not self.settings.a2a_fail_open:
                raise
            return None

    def health(self) -> bool:
        try:
            return bool(self.discover().get("name"))
        except Exception:
            return False


def create_vendor_expert_client(
    database: Database, settings: Settings
) -> DisabledVendorExpertClient | VendorExpertClient:
    if not settings.a2a_enabled:
        return DisabledVendorExpertClient()
    return VendorExpertClient(database, settings)
