from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Paths are resolved from the process working directory."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    data_backend: str = Field(default="sqlite", pattern="^(sqlite|mysql)$")
    database_path: Path = Path("./data/factorypilot.db")
    database_url: str = "mysql://factorypilot:factorypilot@localhost:3306/factorypilot"
    checkpoint_path: Path = Path("./data/checkpoints.db")
    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "factorypilot"
    redis_lock_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    redis_fail_open: bool = True
    task_queue_backend: str = Field(default="local", pattern="^(local|celery)$")
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_queue: str = "factorypilot"
    celery_task_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    allowed_origins: str = "http://localhost:5173,http://localhost:3000"
    agent_mode: str = Field(default="demo", pattern="^(demo|llm)$")
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4.1-mini"
    llm_fail_open: bool = True
    llm_timeout_seconds: float = Field(default=30.0, ge=5, le=120)
    llm_max_retries: int = Field(default=1, ge=0, le=5)
    knowledge_backend: str = Field(default="lexical", pattern="^(lexical|milvus)$")
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str | None = None
    milvus_collection: str = "factorypilot_knowledge"
    milvus_dimension: int = Field(default=128, ge=32, le=4096)
    milvus_fail_open: bool = True
    neo4j_enabled: bool = False
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("factorypilot-graph")
    neo4j_database: str = "neo4j"
    neo4j_fail_open: bool = True
    tool_transport: str = Field(default="inprocess", pattern="^(inprocess|mcp)$")
    mes_mcp_url: str = "http://localhost:8101/mcp"
    knowledge_mcp_url: str = "http://localhost:8102/mcp"
    action_mcp_url: str = "http://localhost:8103/mcp"
    mcp_timeout_seconds: float = Field(default=8.0, ge=1, le=60)
    mcp_max_attempts: int = Field(default=2, ge=1, le=5)
    mcp_trust_env: bool = False
    a2a_enabled: bool = False
    vendor_a2a_card_url: str = "http://localhost:8200/.well-known/agent-card.json"
    vendor_a2a_public_url: str = "http://localhost:8200/a2a"
    vendor_a2a_token: SecretStr = SecretStr("factorypilot-vendor-token")
    a2a_timeout_seconds: float = Field(default=8.0, ge=1, le=60)
    a2a_fail_open: bool = True
    require_gateway_auth: bool = False
    gateway_shared_secret: str = "change-this-in-production"
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: str | None = None
    langsmith_project: str = "factorypilot"
    langsmith_dataset: str = "factorypilot-regression-v1"

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
