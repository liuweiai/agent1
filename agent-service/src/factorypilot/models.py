from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


RunStatus = Literal[
    "queued",
    "running",
    "awaiting_approval",
    "executing",
    "completed",
    "rejected",
    "failed",
]


class RunCreate(BaseModel):
    query: str = Field(min_length=5, max_length=500)
    line_id: str = Field(default="LINE-02", pattern=r"^LINE-\d{2}$")


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str = Field(min_length=2, max_length=50)
    comment: str = Field(default="", max_length=500)


class Evidence(BaseModel):
    id: str
    source_type: Literal["mes", "alarm", "quality", "maintenance", "knowledge"]
    title: str
    excerpt: str
    source_ref: str
    score: float = Field(ge=0, le=1)


class RootCauseHypothesis(BaseModel):
    cause: str
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    evidence_ids: list[str]


class ActionItem(BaseModel):
    tool: str
    title: str
    description: str
    risk: Literal["low", "medium", "high"]
    parameters: dict[str, Any]


class TimelineEvent(BaseModel):
    id: int
    run_id: str
    step: str
    title: str
    status: Literal["running", "completed", "waiting", "failed", "skipped"]
    detail: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RunSummary(BaseModel):
    id: str
    query: str
    line_id: str
    status: RunStatus
    severity: str | None = None
    risk_level: str | None = None
    created_at: str
    updated_at: str


class RunDetail(RunSummary):
    state: dict[str, Any] = Field(default_factory=dict)
    events: list[TimelineEvent] = Field(default_factory=list)
    approval: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
