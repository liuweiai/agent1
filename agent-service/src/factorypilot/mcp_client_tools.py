from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


class MCPToolCallError(RuntimeError):
    pass


class MCPRemoteCaller:
    def __init__(self, url: str, timeout_seconds: float = 8.0, max_attempts: int = 2, trust_env: bool = False):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.trust_env = trust_env

    async def _call_async(self, name: str, arguments: dict[str, Any]) -> Any:
        async with httpx2.AsyncClient(
            trust_env=self.trust_env, follow_redirects=True, timeout=self.timeout_seconds
        ) as http_client:
            transport = streamable_http_client(self.url, http_client=http_client)
            async with Client(transport, read_timeout_seconds=self.timeout_seconds) as client:
                result = await asyncio.wait_for(
                    client.call_tool(name, arguments, read_timeout_seconds=self.timeout_seconds),
                    timeout=self.timeout_seconds + 1,
                )
            if result.is_error:
                detail = " ".join(getattr(block, "text", "") for block in result.content)
                raise MCPToolCallError(f"{name} failed: {detail}")
            structured = result.structured_content
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            if structured is not None:
                return structured
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    return json.loads(text)
            return None

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return asyncio.run(self._call_async(name, arguments))
            except Exception as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    time.sleep(0.15 * (2 ** (attempt - 1)))
        raise MCPToolCallError(
            f"MCP {self.url} tool={name} failed after {self.max_attempts} attempts: {last_error}"
        ) from last_error


class RemoteMesTools:
    def __init__(self, caller: MCPRemoteCaller): self.caller = caller
    def get_line_device(self, run_id: str, line_id: str):
        return self.caller.call("get_line_device", {"run_id": run_id, "line_id": line_id})
    def get_recent_alerts(self, run_id: str, device_id: str, hours: int = 24, limit: int = 20):
        return self.caller.call("get_recent_alerts", {"run_id": run_id, "device_id": device_id, "hours": hours, "limit": limit})
    def get_recent_quality(self, run_id: str, line_id: str, limit: int = 20):
        return self.caller.call("get_recent_quality", {"run_id": run_id, "line_id": line_id, "limit": limit})
    def get_maintenance_history(self, run_id: str, device_id: str, limit: int = 10):
        return self.caller.call("get_maintenance_history", {"run_id": run_id, "device_id": device_id, "limit": limit})


class RemoteKnowledgeTools:
    def __init__(self, caller: MCPRemoteCaller): self.caller = caller
    def search(self, run_id: str, query: str, top_k: int = 4):
        return self.caller.call("search_knowledge", {"run_id": run_id, "query": query, "top_k": top_k})


class RemoteActionTools:
    def __init__(self, caller: MCPRemoteCaller): self.caller = caller
    def create_maintenance_order(self, run_id: str, device_id: str, priority: str, title: str, description: str, approval: dict[str, Any]):
        return self.caller.call("create_maintenance_order", {"run_id": run_id, "device_id": device_id, "priority": priority, "title": title, "description": description})
    def tighten_inspection(self, run_id: str, line_id: str, interval_minutes: int, approval: dict[str, Any]):
        return self.caller.call("tighten_inspection", {"run_id": run_id, "line_id": line_id, "interval_minutes": interval_minutes})


def create_remote_tools(settings: Any):
    options = {
        "timeout_seconds": settings.mcp_timeout_seconds,
        "max_attempts": settings.mcp_max_attempts,
        "trust_env": settings.mcp_trust_env,
    }
    return (
        RemoteMesTools(MCPRemoteCaller(settings.mes_mcp_url, **options)),
        RemoteKnowledgeTools(MCPRemoteCaller(settings.knowledge_mcp_url, **options)),
        RemoteActionTools(MCPRemoteCaller(settings.action_mcp_url, **options)),
    )
