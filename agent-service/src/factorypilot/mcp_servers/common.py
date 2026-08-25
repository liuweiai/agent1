from __future__ import annotations

import os
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings

from ..config import get_settings
from ..database import Database
from ..mysql_database import create_database as create_configured_database


def create_database() -> Database:
    settings = get_settings()
    settings.ensure_directories()
    database = create_configured_database(settings)
    database.initialize()
    return database


def run_server(server: Any) -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        server.run(
            transport="streamable-http",
            host=os.getenv("MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("MCP_PORT", "8000")),
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=os.getenv("MCP_DNS_PROTECTION", "true").lower()
                == "true",
                allowed_hosts=[
                    value.strip()
                    for value in os.getenv(
                        "MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,[::1]:*"
                    ).split(",")
                    if value.strip()
                ],
            ),
        )
    else:
        server.run(transport="stdio")
