from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..tools import SafeActionTools, ToolValidationError
from .common import create_database, run_server


database = create_database()
tools = SafeActionTools(database)
mcp = MCPServer(
    "factorypilot-action-mcp",
    version="0.1.0",
    instructions="执行模拟 MES 写操作。服务端从持久化任务读取审批，不信任调用方传入的 approved 标志。",
)


@mcp.tool(structured_output=True)
def health() -> dict[str, str]:
    """MCP 协议级就绪检查，不执行任何写操作。"""
    database.fetch_one("SELECT 1 AS ok")
    return {"status": "ok", "server": "action-mcp"}


def _server_verified_approval(run_id: str) -> dict:
    run = database.get_run(run_id)
    approval = run.get("approval") if run else None
    if not approval or not approval.get("approved") or not approval.get("approver"):
        raise ToolValidationError("服务端未找到该任务的有效批准记录")
    return approval


@mcp.tool(structured_output=True)
def create_maintenance_order(
    run_id: str,
    device_id: str,
    priority: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    """在服务端复核人工审批后创建维修工单。"""
    return tools.create_maintenance_order(
        run_id,
        device_id,
        priority,
        title,
        description,
        _server_verified_approval(run_id),
    )


@mcp.tool(structured_output=True)
def tighten_inspection(run_id: str, line_id: str, interval_minutes: int) -> dict[str, Any]:
    """在服务端复核人工审批后调整模拟产线抽检周期。"""
    return tools.tighten_inspection(
        run_id, line_id, interval_minutes, _server_verified_approval(run_id)
    )


def main() -> None:
    run_server(mcp)


if __name__ == "__main__":
    main()
