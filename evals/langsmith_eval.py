from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "agent-service" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from langsmith import Client
from langsmith.utils import LangSmithError, LangSmithNotFoundError

from factorypilot.config import Settings
from factorypilot.milvus_knowledge import create_knowledge_tools
from factorypilot.mysql_database import create_database
from factorypilot.triage import classify_anomaly


def load_cases() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("cases.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sync_dataset(client: Client, dataset_name: str) -> None:
    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
    except LangSmithNotFoundError:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="FactoryPilot 意图分类与知识检索回归集；全部为合成 MES 数据。",
            data_type="kv",
        )
    existing = {
        example.inputs.get("id")
        for example in client.list_examples(dataset_id=dataset.id)
    }
    examples = []
    for case in load_cases():
        if case["id"] in existing:
            continue
        examples.append(
            {
                "inputs": {"id": case["id"], "kind": case["kind"], "query": case["query"]},
                "outputs": {"expected": case["expected"]},
                "metadata": {"source": "synthetic", "suite": case["kind"], "version": "v1"},
            }
        )
    if examples:
        client.create_examples(dataset_id=dataset.id, examples=examples)
    client.update_dataset_tag(
        dataset_name=dataset_name,
        as_of=datetime.now(timezone.utc),
        tag="baseline-v1",
    )
    print(f"Dataset ready: {dataset_name}; added={len(examples)}")


class EvaluationTarget:
    def __init__(self):
        settings = Settings()
        self.database = create_database(settings)
        self.database.initialize()
        self.knowledge = create_knowledge_tools(self.database, settings)

    def __call__(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if inputs["kind"] == "intent":
            prediction, severity = classify_anomaly(inputs["query"])
            return {"prediction": prediction, "severity": severity}
        results = self.knowledge.search(inputs["id"], inputs["query"], top_k=4)
        return {
            "retrieved_ids": [item["id"] for item in results],
            "citations": [f"{item['id']}#{item['section']}" for item in results],
        }


def exact_or_recall(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    expected = reference_outputs["expected"]
    if inputs["kind"] == "intent":
        score = outputs.get("prediction") == expected
        key = "intent_accuracy"
    else:
        score = expected in outputs.get("retrieved_ids", [])
        key = "recall_at_4"
    return {"key": key, "score": int(score)}


def citation_present(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    if inputs["kind"] != "retrieval":
        return {"key": "citation_present", "score": 1}
    expected = reference_outputs["expected"]
    score = any(citation.startswith(expected + "#") for citation in outputs.get("citations", []))
    return {"key": "citation_present", "score": int(score)}


def main() -> None:
    if not os.getenv("LANGSMITH_API_KEY"):
        raise SystemExit(
            "缺少 LANGSMITH_API_KEY。先设置 Key；若只需本地评测，请运行 evals/evaluate.py。"
        )
    settings = Settings()
    client = Client(
        api_url=settings.langsmith_endpoint,
        workspace_id=settings.langsmith_workspace_id,
    )
    try:
        sync_dataset(client, settings.langsmith_dataset)
        results = client.evaluate(
            EvaluationTarget(),
            data=settings.langsmith_dataset,
            evaluators=[exact_or_recall, citation_present],
            experiment_prefix="factorypilot-regression",
            max_concurrency=1,
            metadata={
                "profile": settings.agent_mode,
                "database": settings.data_backend,
                "knowledge": settings.knowledge_backend,
                "tool_transport": settings.tool_transport,
                "models": [settings.openai_model] if settings.agent_mode == "llm" else ["demo-rca"],
                "tools": ["mes-mcp", "knowledge-mcp", "action-mcp"],
            },
        )
        print(results)
    except LangSmithError as exc:
        message = str(exc)
        if "403" in message:
            raise SystemExit(
                "LangSmith 返回 403：请确认 Key 已完成创建；多工作区账号还需设置 "
                "LANGSMITH_WORKSPACE_ID，非美国区需设置 LANGSMITH_ENDPOINT。"
            ) from None
        raise SystemExit(f"LangSmith 请求失败：{type(exc).__name__}") from None


if __name__ == "__main__":
    main()
