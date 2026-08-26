from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..tools import MesReadTools
from .common import create_database, run_server


database = create_database()
tools = MesReadTools(database)
mcp = MCPServer(
    "factorypilot-mes-mcp",
    version="0.1.0",
    instructions="只提供参数化、只读的 MES 设备、告警、批次质量和维修记录查询；不接受任意 SQL。",
)


@mcp.tool(structured_output=True)
def health() -> dict[str, str]:
    """MCP 协议级就绪检查。"""
    database.fetch_one("SELECT 1 AS ok")
    return {"status": "ok", "server": "mes-mcp"}


@mcp.tool(structured_output=True)
def get_line_device(run_id: str, line_id: str) -> dict[str, Any]:
    """按白名单产线编号查询设备台账。"""
    return tools.get_line_device(run_id, line_id)


@mcp.tool(structured_output=True)
def get_recent_alerts(run_id: str, device_id: str, hours: int = 24, limit: int = 20) -> list[dict[str, Any]]:
    """查询设备近期告警；时间窗口和条数均受策略限制。"""
    return tools.get_recent_alerts(run_id, device_id, hours, limit)


@mcp.tool(structured_output=True)
def get_recent_quality(run_id: str, line_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """查询最近批次良率、缺陷与受控过程参数。"""
    return tools.get_recent_quality(run_id, line_id, limit)


@mcp.tool(structured_output=True)
def get_maintenance_history(run_id: str, device_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """查询指定设备的历史维修记录。"""
    return tools.get_maintenance_history(run_id, device_id, limit)


def main() -> None:
    run_server(mcp)


if __name__ == "__main__":
    main()
