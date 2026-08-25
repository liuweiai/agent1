from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import SecretStr


class RcaEngine(Protocol):
    def analyze(self, state: dict[str, Any]) -> list[dict[str, Any]]: ...


class DemoRcaEngine:
    """Evidence-grounded deterministic RCA used when no external model key is set."""

    def analyze(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        evidence = state.get("evidence", [])
        by_text = " ".join(item.get("excerpt", "") for item in evidence)
        hypotheses: list[dict[str, Any]] = []
        if "能量衰减" in by_text or "镜片" in by_text:
            hypotheses.append(
                {
                    "cause": "保护镜片污染导致光路能量衰减",
                    "confidence": 0.89,
                    "reasoning": "近期 OPT-207 告警、打码模糊缺陷与同机历史维修记录形成相互独立的证据链；SOP 也给出了相同阈值和处置步骤。",
                    "evidence_ids": [
                        item["id"]
                        for item in evidence
                        if any(key in item.get("excerpt", "") for key in ("能量", "镜片", "模糊"))
                    ][:5],
                }
            )
        hypotheses.append(
            {
                "cause": "打码速度提升放大了光学系统波动",
                "confidence": 0.72,
                "reasoning": "异常批次打码速度为 135，而稳定批次通常为 120；它与光学衰减叠加后更容易产生模糊和偏移。",
                "evidence_ids": [item["id"] for item in evidence if item["source_type"] == "quality"][:3],
            }
        )
        hypotheses.append(
            {
                "cause": "视觉定位基准轻微偏移",
                "confidence": 0.58,
                "reasoning": "存在 POS-112 告警和 position_offset 缺陷，但无法单独解释全部良率下降，因此列为次要假设。",
                "evidence_ids": [item["id"] for item in evidence if "偏移" in item.get("excerpt", "")][:3],
            }
        )
        return hypotheses


class OpenAIRcaEngine:
    """OpenAI-compatible structured-output adapter (OpenAI, DeepSeek, etc.)."""

    def __init__(
        self,
        model: str,
        api_key: SecretStr | None,
        base_url: str | None,
        fail_open: bool = True,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
    ):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - optional profile
            raise RuntimeError("llm 模式需要安装: pip install -e './agent-service[llm]'") from exc
        self.model = ChatOpenAI(
            model=model,
            temperature=0,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        self.model_name = model
        self.fail_open = fail_open

    def analyze(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        from pydantic import BaseModel, Field

        class Hypothesis(BaseModel):
            cause: str
            confidence: float = Field(ge=0, le=1)
            reasoning: str
            evidence_ids: list[str]

        class Result(BaseModel):
            hypotheses: list[Hypothesis]

        prompt = (
            "你是 MES 根因分析专家。只能使用给定 evidence，禁止补造事实。"
            "输出最多3个根因假设；每个假设必须引用 evidence id，并对冲突或不足证据降低置信度。\n"
            "必须只输出满足下列 JSON Schema 的 JSON 对象，不要输出 Markdown。\n"
            f"JSON Schema：{json.dumps(Result.model_json_schema(), ensure_ascii=False)}\n"
            f"异常：{state.get('query')}\n证据：{state.get('evidence', [])}"
        )
        try:
            result = self.model.with_structured_output(
                Result, method="json_mode"
            ).invoke(prompt)
        except Exception as exc:
            if not self.fail_open:
                raise
            hypotheses = DemoRcaEngine().analyze(state)
            for item in hypotheses:
                item["model_source"] = "deterministic-fallback"
                item["degraded"] = True
                item["degraded_reason"] = type(exc).__name__
            return hypotheses

        hypotheses = [item.model_dump() for item in result.hypotheses]
        for item in hypotheses:
            item["model_source"] = self.model_name
            item["degraded"] = False
        return hypotheses
