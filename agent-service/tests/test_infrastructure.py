from __future__ import annotations

import math
import sys
from decimal import Decimal
from types import ModuleType

import pytest
from mcp import Client
from pydantic import SecretStr

from factorypilot.mcp_servers.mes_server import mcp as mes_mcp
from factorypilot.milvus_knowledge import HashEmbeddingProvider, HybridReranker
from factorypilot.mysql_database import MySQLConnectionAdapter, MySQLDatabase


def test_openai_compatible_engine_forwards_deepseek_configuration(monkeypatch) -> None:
    captured: dict = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    module = ModuleType("langchain_openai")
    module.ChatOpenAI = FakeChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", module)

    from factorypilot.analysis_engine import OpenAIRcaEngine

    OpenAIRcaEngine(
        model="deepseek-v4-flash",
        api_key=SecretStr("test-only-key"),
        base_url="https://api.deepseek.com",
    )

    assert captured["model"] == "deepseek-v4-flash"
    assert captured["temperature"] == 0
    assert captured["api_key"].get_secret_value() == "test-only-key"
    assert captured["base_url"] == "https://api.deepseek.com"


def test_openai_compatible_engine_falls_back_without_leaking_error(monkeypatch) -> None:
    class FailingChatOpenAI:
        def __init__(self, **kwargs):
            pass

        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, prompt):
            raise ConnectionError("provider unavailable")

    module = ModuleType("langchain_openai")
    module.ChatOpenAI = FailingChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", module)

    from factorypilot.analysis_engine import OpenAIRcaEngine

    engine = OpenAIRcaEngine(
        model="deepseek-v4-flash",
        api_key=SecretStr("test-only-key"),
        base_url="https://api.deepseek.com",
        fail_open=True,
    )
    result = engine.analyze(
        {
            "query": "激光打码良率下降",
            "evidence": [
                {
                    "id": "E-1",
                    "source_type": "quality",
                    "excerpt": "镜片污染导致能量衰减和打码模糊",
                }
            ],
        }
    )

    assert result[0]["degraded"] is True
    assert result[0]["model_source"] == "deterministic-fallback"
    assert result[0]["degraded_reason"] == "ConnectionError"


@pytest.mark.asyncio
async def test_mcp_server_uses_real_protocol_and_structured_output() -> None:
    async with Client(mes_mcp) as client:
        health = await client.call_tool("health", {})
        result = await client.call_tool(
            "get_line_device", {"run_id": "MCP-TEST", "line_id": "LINE-02"}
        )
    assert health.structured_content == {"status": "ok", "server": "mes-mcp"}
    assert result.is_error is False
    assert result.structured_content["id"] == "DEV-L02-LASER"


def test_hash_embedding_is_deterministic_and_normalized() -> None:
    provider = HashEmbeddingProvider(64)
    first = provider.embed("激光镜片能量衰减")
    second = provider.embed("激光镜片能量衰减")
    assert first == second
    assert math.isclose(sum(value * value for value in first), 1.0, rel_tol=1e-6)


def test_hybrid_reranker_combines_dense_and_lexical_scores() -> None:
    documents = {
        "A": {"id": "A", "title": "A", "section": "1", "content": "a", "version": "v1"},
        "B": {"id": "B", "title": "B", "section": "1", "content": "b", "version": "v1"},
    }
    result = HybridReranker(dense_weight=0.6, lexical_weight=0.4).rank(
        documents, dense_scores={"A": 0.9, "B": 0.5}, lexical_scores={"A": 0.2, "B": 1.0}, top_k=2
    )
    assert result[0]["id"] == "B"
    assert result[0]["retrieval"]["backend"] == "milvus-hybrid"


def test_mysql_adapter_preserves_parameterization_and_ignore_semantics() -> None:
    sql = MySQLConnectionAdapter._sql("INSERT OR IGNORE INTO runs(id) VALUES (?)")
    assert sql == "INSERT IGNORE INTO runs(id) VALUES (%s)"


def test_mysql_decimal_values_are_normalized_for_mcp_json() -> None:
    row = MySQLDatabase._normalize_row({"yield_rate": Decimal("91.50"), "count": 3})
    assert row == {"yield_rate": 91.5, "count": 3}
