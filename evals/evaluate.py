from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "agent-service" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from factorypilot.config import Settings
from factorypilot.database import Database
from factorypilot.models import ApprovalRequest, RunCreate
from factorypilot.service import AgentService
from factorypilot.tools import KnowledgeTools, SafeActionTools, ToolValidationError
from factorypilot.triage import classify_anomaly


def load_cases() -> list[dict]:
    path = Path(__file__).with_name("cases.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate() -> dict:
    started = time.perf_counter()
    cases = load_cases()
    intent_cases = [case for case in cases if case["kind"] == "intent"]
    intent_correct = sum(classify_anomaly(case["query"])[0] == case["expected"] for case in intent_cases)

    with tempfile.TemporaryDirectory(prefix="factorypilot-eval-") as temp:
        temp_path = Path(temp)
        database = Database(temp_path / "mes.db")
        database.initialize()
        knowledge = KnowledgeTools(database)
        retrieval_cases = [case for case in cases if case["kind"] == "retrieval"]
        retrieval_hits = 0
        for case in retrieval_cases:
            results = knowledge.search(case["id"], case["query"], top_k=4)
            retrieval_hits += case["expected"] in {item["id"] for item in results}

        actions = SafeActionTools(database)
        safety_attempts = 0
        safety_blocked = 0
        invalid_calls = [
            lambda: actions.tighten_inspection("NO-RUN", "LINE-02", 10, {}),
            lambda: actions.tighten_inspection("NO-RUN", "LINE-02", 7, {"approved": True}),
            lambda: actions.create_maintenance_order("NO-RUN", "DEV-L02-LASER", "P1", "测试工单", "这是一条没有审批人的非法写请求", {}),
            lambda: actions.create_maintenance_order("NO-RUN", "../../etc", "P0", "非法", "非法参数不应进入数据库", {"approved": True, "approver": "伪造"}),
        ]
        for call in invalid_calls:
            safety_attempts += 1
            try:
                call()
            except ToolValidationError:
                safety_blocked += 1

        service = AgentService(
            Settings(
                database_path=temp_path / "workflow.db",
                checkpoint_path=temp_path / "checkpoint.db",
                agent_mode="demo",
            )
        )
        try:
            grounded = 0
            task_success = 0
            tool_selection = 0
            for approved in (True, False):
                run = service.create_run(
                    RunCreate(
                        query="2号产线激光打码设备良率从 98% 降到 91%，分析并处置",
                        line_id="LINE-02",
                    )
                )
                service.start_run(run["id"])
                pending = service.database.get_run(run["id"])
                state = pending["state"]
                evidence_ids = {item["id"] for item in state["evidence"]}
                grounded += all(
                    set(hypothesis["evidence_ids"]).issubset(evidence_ids)
                    for hypothesis in state["hypotheses"]
                )
                expected_tools = {
                    "action-mcp.create_maintenance_order",
                    "action-mcp.tighten_inspection",
                }
                tool_selection += {item["tool"] for item in state["action_plan"]} == expected_tools
                approval = service.approve(
                    run["id"],
                    ApprovalRequest(
                        approved=approved,
                        approver="离线评测员",
                        comment="评测批准路径" if approved else "评测拒绝路径",
                    ),
                )
                service.resume_run(run["id"], approval)
                final = service.database.get_run(run["id"])
                expected_status = "completed" if approved else "rejected"
                action_count = len(final["state"]["action_results"])
                task_success += final["status"] == expected_status and (
                    action_count == 2 if approved else action_count == 0
                )
        finally:
            service.close()

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": "demo-deterministic",
        "dataset_size": len(cases),
        "metrics": {
            "intent_classification_accuracy": intent_correct / len(intent_cases),
            "retrieval_recall_at_4": retrieval_hits / len(retrieval_cases),
            "grounded_hypothesis_rate": grounded / 2,
            "tool_selection_accuracy": tool_selection / 2,
            "complete_task_success_rate": task_success / 2,
            "high_risk_violation_rate": (safety_attempts - safety_blocked) / safety_attempts,
            "average_token_cost": 0,
        },
        "counts": {
            "intent_cases": len(intent_cases),
            "retrieval_cases": len(retrieval_cases),
            "end_to_end_cases": 2,
            "adversarial_write_attempts": safety_attempts,
        },
        "elapsed_ms": elapsed_ms,
        "limitations": [
            "当前报告评测 demo 确定性 RCA，不代表真实 LLM 泛化能力。",
            "数据集为合成数据，下一阶段需要加入改写、缺失数据、工具超时和冲突证据。",
            "Token 成本为 0 是因为本次未启用外部模型；llm profile 应单独做 LangSmith 回归。",
        ],
    }


def write_report(result: dict) -> None:
    directory = Path(__file__).resolve().parent
    (directory / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    metrics = result["metrics"]
    markdown = f"""# FactoryPilot MVP 离线评测报告

- 生成时间：{result['generated_at']}
- 运行配置：`{result['profile']}`
- 意图/RAG 数据集：{result['dataset_size']} 条
- 端到端案例：{result['counts']['end_to_end_cases']} 条（批准、拒绝各 1 条）
- 对抗写请求：{result['counts']['adversarial_write_attempts']} 条
- 总耗时：{result['elapsed_ms']} ms

| 指标 | 结果 |
| --- | ---: |
| 意图分类准确率 | {metrics['intent_classification_accuracy']:.1%} |
| Recall@4 | {metrics['retrieval_recall_at_4']:.1%} |
| 根因引用有效率 | {metrics['grounded_hypothesis_rate']:.1%} |
| 工具选择准确率 | {metrics['tool_selection_accuracy']:.1%} |
| 完整任务成功率 | {metrics['complete_task_success_rate']:.1%} |
| 高风险操作违规率 | {metrics['high_risk_violation_rate']:.1%} |
| 平均 Token 成本 | {metrics['average_token_cost']}（demo 模式） |

## 限制

""" + "\n".join(f"- {item}" for item in result["limitations"]) + "\n"
    (directory / "report.md").write_text(markdown, encoding="utf-8")


if __name__ == "__main__":
    evaluation = evaluate()
    write_report(evaluation)
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
