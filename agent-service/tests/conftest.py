from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_external_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests deterministic and prevent accidental external traces."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
