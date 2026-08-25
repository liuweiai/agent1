from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..config import get_settings
from ..milvus_knowledge import create_knowledge_tools
from .common import create_database, run_server


database = create_database()
tools = create_knowledge_tools(database, get_settings())
mcp = MCPServer(
    "factorypilot-knowledge-mcp",
    version="0.1.0",
    instructions="检索设备手册和异常处置 SOP，返回文档、版本、章节和相关度。",
)


@mcp.tool(structured_output=True)
def health() -> dict[str, str]:
    """MCP 协议级就绪检查。"""
    database.fetch_one("SELECT 1 AS ok")
    return {"status": "ok", "server": "knowledge-mcp"}


@mcp.tool(structured_output=True)
def search_knowledge(run_id: str, query: str, top_k: int = 4) -> list[dict[str, Any]]:
    """对手册和 SOP 执行受限 top-k 检索，结果包含段落级引用。"""
    return tools.search(run_id, query, top_k)


def main() -> None:
    run_server(mcp)


if __name__ == "__main__":
    main()
