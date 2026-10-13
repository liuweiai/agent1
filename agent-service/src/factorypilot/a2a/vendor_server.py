from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis import Redis

from ..config import get_settings


settings = get_settings()
app = FastAPI(
    title="LM-5000 Equipment Vendor Expert Agent",
    version="1.0.0",
    description="Independent A2A equipment-vendor expert for FactoryPilot",
)


class VendorTaskStore:
    def __init__(self):
        self.memory: dict[str, dict[str, Any]] = {}
        self.redis: Redis | None = None
        if settings.redis_enabled:
            try:
                self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
                self.redis.ping()
            except Exception:
                if not settings.redis_fail_open:
                    raise
                self.redis = None

    def get(self, task_id: str) -> dict[str, Any] | None:
        if self.redis:
            raw = self.redis.get(f"{settings.redis_key_prefix}:a2a:task:{task_id}")
            return json.loads(raw) if raw else None
        return self.memory.get(task_id)

    def put(self, task: dict[str, Any]) -> None:
        if self.redis:
            self.redis.setex(
                f"{settings.redis_key_prefix}:a2a:task:{task['id']}",
                86400,
                json.dumps(task, ensure_ascii=False),
            )
        else:
            self.memory[task["id"]] = task


store = VendorTaskStore()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rpc_error(request_id: Any, code: int, message: str, status_code: int = 200):
    return JSONResponse(
        status_code=status_code,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
    )


def extract_context(message: dict[str, Any]) -> dict[str, Any]:
    for part in message.get("parts", []):
        if part.get("kind") == "data" and isinstance(part.get("data"), dict):
            return part["data"]
    text = " ".join(
        str(part.get("text", ""))
        for part in message.get("parts", [])
        if part.get("kind") == "text"
    )
    return {"query": text, "model": "LM-5000"}


def diagnose(context: dict[str, Any]) -> dict[str, Any]:
    query = str(context.get("query", ""))
    alarm_codes = {str(code) for code in context.get("alarm_codes", [])}
    likely_causes = []
    if "OPT-207" in alarm_codes or any(word in query for word in ("良率", "模糊", "能量", "镜片")):
        likely_causes.append(
            {
                "cause": "保护镜片污染或光路衰减",
                "confidence": 0.91,
                "manufacturer_code": "LM5K-OPTICS-01",
                "basis": "OPT-207 与打码模糊组合符合厂商光路衰减故障模式",
            }
        )
    if "POS-112" in alarm_codes or "偏移" in query:
        likely_causes.append(
            {
                "cause": "视觉定位基准漂移",
                "confidence": 0.78,
                "manufacturer_code": "LM5K-VISION-03",
                "basis": "POS-112 表示定位偏差超过厂商建议阈值",
            }
        )
    if not likely_causes:
        likely_causes.append(
            {
                "cause": "需要补充设备告警码和标准样件结果",
                "confidence": 0.35,
                "manufacturer_code": "LM5K-DATA-REQUIRED",
                "basis": "现有上下文不足以匹配单一厂商故障模式",
            }
        )
    return {
        "vendor": "FactoryPilot Demo Equipment Co.",
        "model": context.get("model", "LM-5000"),
        "firmware_scope": "LM-5000 firmware 4.x",
        "likely_causes": likely_causes[:3],
        "inspection_steps": [
            "执行停机和能源隔离后检查保护镜片污染",
            "记录标准样件打码前后的功率反馈值",
            "复核视觉定位基准并保留校准结果",
        ],
        "safety_constraints": [
            "激光器工作状态下禁止打开光路组件",
            "任何参数修改必须由现场主管审批并记录前后值",
        ],
        "reference": "LM-5000 Vendor Service Bulletin V4.3",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "lm5000-vendor-expert", "protocol": "A2A-0.3.0"}


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict[str, Any]:
    return {
        "protocolVersion": "0.3.0",
        "name": "LM-5000 Equipment Vendor Expert",
        "description": "Provides manufacturer fault patterns, inspection steps and safety constraints for LM-5000 laser marking equipment.",
        "url": settings.vendor_a2a_public_url,
        "preferredTransport": "JSONRPC",
        "supportedInterfaces": [
            {"url": settings.vendor_a2a_public_url, "transport": "JSONRPC"}
        ],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "opaque"}
        },
        "security": [{"bearerAuth": []}],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "lm5000-fault-diagnosis",
                "name": "LM-5000 fault diagnosis",
                "description": "Matches alarm codes and symptoms to manufacturer fault patterns and safe inspection steps.",
                "tags": ["laser-marking", "equipment", "fault-diagnosis", "safety"],
                "examples": ["Analyze OPT-207 energy attenuation with marking blur"],
                "inputModes": ["application/json", "text/plain"],
                "outputModes": ["application/json"],
            }
        ],
    }


@app.post("/a2a")
async def a2a(request: Request):
    token = settings.vendor_a2a_token.get_secret_value()
    supplied = request.headers.get("authorization", "")
    if token and not hmac.compare_digest(supplied, f"Bearer {token}"):
        return rpc_error(None, -32001, "Unauthorized", 401)
    try:
        body = await request.json()
    except Exception:
        return rpc_error(None, -32700, "Parse error")
    request_id = body.get("id")
    if body.get("jsonrpc") != "2.0" or not body.get("method"):
        return rpc_error(request_id, -32600, "Invalid Request")

    if body["method"] == "tasks/get":
        task_id = str(body.get("params", {}).get("id") or body.get("params", {}).get("taskId") or "")
        task = store.get(task_id)
        if not task:
            return rpc_error(request_id, -32001, "Task not found")
        return {"jsonrpc": "2.0", "id": request_id, "result": task}

    if body["method"] != "message/send":
        return rpc_error(request_id, -32601, "Method not found")
    message = body.get("params", {}).get("message", {})
    message_id = str(message.get("messageId", ""))
    if not message_id or message.get("role") != "user":
        return rpc_error(request_id, -32602, "messageId and user role are required")

    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"factorypilot-vendor:{message_id}"))
    existing = store.get(task_id)
    if existing:
        return {"jsonrpc": "2.0", "id": request_id, "result": existing}
    context = extract_context(message)
    result = diagnose(context)
    task = {
        "id": task_id,
        "contextId": str(message.get("contextId") or uuid.uuid4()),
        "status": {"state": "completed", "timestamp": now()},
        "artifacts": [
            {
                "artifactId": str(uuid.uuid4()),
                "name": "LM-5000 manufacturer diagnosis",
                "parts": [{"kind": "data", "data": result}],
            }
        ],
        "history": [message],
    }
    store.put(task)
    return {"jsonrpc": "2.0", "id": request_id, "result": task}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.app_host, port=8200)
