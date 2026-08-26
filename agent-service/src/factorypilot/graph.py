from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .analysis_engine import RcaEngine
from .database import Database
from .models import utc_now
from .production_analytics import analyze_production_window, evaluate_recovery
from .tools import KnowledgeTools, MesReadTools, SafeActionTools
from .triage import classify_anomaly


class AgentState(TypedDict, total=False):
    run_id: str
    query: str
    line_id: str
    device_id: str
    anomaly_type: str
    severity: str
    risk_level: str
    device_context: dict[str, Any]
    alarms: list[dict[str, Any]]
    quality: list[dict[str, Any]]
    maintenance: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    production_analysis: dict[str, Any]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    hypotheses: list[dict[str, Any]]
    action_plan: list[dict[str, Any]]
    approval: dict[str, Any]
    action_results: list[dict[str, Any]]
    verification: dict[str, Any]
    report: dict[str, Any]


class FactoryPilotGraph:
    def __init__(
        self,
        database: Database,
        mes_tools: MesReadTools,
        knowledge_tools: KnowledgeTools,
        action_tools: SafeActionTools,
        rca_engine: RcaEngine,
    ):
        self.database = database
        self.mes = mes_tools
        self.knowledge = knowledge_tools
        self.actions = action_tools
        self.rca_engine = rca_engine

    def build(self, checkpointer: Any):
        builder = StateGraph(AgentState)
        builder.add_node("triage", self.triage)
        builder.add_node("mes_data", self.collect_mes_data)
        builder.add_node("alarm_data", self.collect_alarm_data)
        builder.add_node("maintenance_data", self.collect_maintenance_data)
        builder.add_node("knowledge_data", self.collect_knowledge_data)
        builder.add_node("statistical_analysis", self.analyze_production_data)
        builder.add_node("rca", self.root_cause_analysis)
        builder.add_node("plan", self.create_action_plan)
        builder.add_node("approval", self.request_approval)
        builder.add_node("execute", self.execute_actions)
        builder.add_node("reject", self.reject_actions)
        builder.add_node("verify", self.verify_result)
        builder.add_node("report", self.generate_report)

        builder.add_edge(START, "triage")
        for node in ("mes_data", "alarm_data", "maintenance_data", "knowledge_data"):
            builder.add_edge("triage", node)
        builder.add_edge(
            ["mes_data", "alarm_data", "maintenance_data", "knowledge_data"],
            "statistical_analysis",
        )
        builder.add_edge("statistical_analysis", "rca")
        builder.add_edge("rca", "plan")
        builder.add_edge("plan", "approval")
        builder.add_edge("execute", "verify")
        builder.add_edge("verify", "report")
        builder.add_edge("reject", "report")
        builder.add_edge("report", END)
        return builder.compile(checkpointer=checkpointer)

    def _event(
        self,
        state: AgentState,
        step: str,
        title: str,
        status: str,
        detail: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.database.emit_event(
            state["run_id"], step, title, status, detail, payload, f"{step}:{status}"
        )

    def triage(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "triage", "异常分诊", "running", "TriageAgent 正在识别异常类型和影响范围")
        query = state["query"]
        anomaly_type, severity = classify_anomaly(query)
        device_id = f"DEV-L{state['line_id'][-2:]}-LASER"
        result = {"anomaly_type": anomaly_type, "severity": severity, "device_id": device_id}
        self.database.update_run(state["run_id"], status="running", severity=severity)
        self._event(
            state,
            "triage",
            "异常分诊",
            "completed",
            f"识别为良率异常，等级 {severity}，范围 {state['line_id']} / {device_id}",
            result,
        )
        return result

    def collect_mes_data(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "mes_data", "MES 生产与质量数据", "running", "调用只读 mes-mcp 白名单查询")
        device = self.mes.get_line_device(state["run_id"], state["line_id"])
        quality = self.mes.get_recent_quality(state["run_id"], state["line_id"], 20)
        evidence = []
        if device:
            evidence.append(
                {
                    "id": "MES-DEVICE-1",
                    "source_type": "mes",
                    "title": f"设备台账：{device['name']}",
                    "excerpt": f"型号 {device['model']}，累计运行 {device['runtime_hours']} 小时，状态 {device['status']}",
                    "source_ref": f"devices/{device['id']}",
                    "score": 0.92,
                }
            )
        if quality:
            rates = [row["yield_rate"] for row in quality]
            defects: dict[str, int] = {}
            for row in quality:
                defects[row["defect_type"]] = defects.get(row["defect_type"], 0) + row["defect_qty"]
            evidence.append(
                {
                    "id": "MES-QUALITY-1",
                    "source_type": "quality",
                    "title": "最近 20 个批次质量与过程参数",
                    "excerpt": f"平均良率 {sum(rates)/len(rates):.2f}%，范围 {min(rates):.2f}%~{max(rates):.2f}%；主要缺陷 {defects}；包含打码速度、激光功率和温度采样",
                    "source_ref": f"quality/{state['line_id']}?batches=20",
                    "score": 0.98,
                }
            )
        self._event(state, "mes_data", "MES 生产与质量数据", "completed", f"获得 {len(quality)} 个批次与设备台账", {"records": len(quality)})
        return {"device_context": device or {}, "quality": quality, "evidence": evidence}

    def collect_alarm_data(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "alarm_data", "设备告警分析", "running", "读取设备近 24 小时告警并提取异常模式")
        alarms = self.mes.get_recent_alerts(state["run_id"], state["device_id"], 24, 20)
        evidence = [
            {
                "id": f"ALARM-{row['id']}",
                "source_type": "alarm",
                "title": f"设备告警 {row['code']}",
                "excerpt": f"{row['occurred_at']}：{row['message']}",
                "source_ref": f"alerts/{row['id']}",
                "score": 0.97 if row["code"] == "OPT-207" else 0.82,
            }
            for row in alarms[:6]
        ]
        self._event(state, "alarm_data", "设备告警分析", "completed", f"发现 {len(alarms)} 条近期告警，其中光路与定位告警优先级最高", {"records": len(alarms)})
        return {"alarms": alarms, "evidence": evidence}

    def collect_maintenance_data(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "maintenance_data", "维修记录分析", "running", "匹配同设备历史相似故障")
        records = self.mes.get_maintenance_history(state["run_id"], state["device_id"], 10)
        evidence = [
            {
                "id": f"MAINT-{row['id']}",
                "source_type": "maintenance",
                "title": f"维修记录 {row['id']}",
                "excerpt": f"{row['description']}；处理：{row['resolution']}；完成时间 {row['completed_at']}",
                "source_ref": f"maintenance/{row['id']}",
                "score": 0.94 if row["id"] == "MR-DEMO-002" else 0.72,
            }
            for row in records[:5]
        ]
        self._event(state, "maintenance_data", "维修记录分析", "completed", f"匹配到 {len(records)} 条历史维修记录")
        return {"maintenance": records, "evidence": evidence}

    def collect_knowledge_data(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "knowledge_data", "SOP / 手册检索", "running", "knowledge-mcp 正在执行混合检索与重排")
        documents = self.knowledge.search(state["run_id"], state["query"] + " 光路 镜片 审批", 4)
        evidence = [
            {
                "id": f"DOC-{doc['id']}",
                "source_type": "knowledge",
                "title": doc["title"],
                "excerpt": doc["content"],
                "source_ref": f"{doc['id']}#{doc['section']} ({doc['version']})",
                "score": doc["score"],
            }
            for doc in documents
        ]
        self._event(state, "knowledge_data", "SOP / 手册检索", "completed", f"召回并重排 {len(documents)} 个相关段落，保留文档版本和章节引用")
        return {"documents": documents, "evidence": evidence}

    def analyze_production_data(self, state: AgentState) -> dict[str, Any]:
        self._event(
            state,
            "statistical_analysis",
            "确定性生产统计分析",
            "running",
            "计算20批次基线、异常窗口、参数相关性与告警时间重合",
        )
        analysis = analyze_production_window(
            state.get("quality", []), state.get("alarms", [])
        )
        baseline = analysis["baseline"]
        anomaly = analysis["anomaly"]
        overlap = analysis["alarm_overlap"]
        correlations = analysis["correlations"]

        correlation_text = "；".join(
            f"{item['label']} r={item['coefficient'] if item['coefficient'] is not None else 'N/A'}（{item['interpretation']}）"
            for item in correlations.values()
        )
        evidence = [
            {
                "id": "STAT-WINDOW-1",
                "source_type": "analytics",
                "title": "稳定基线与异常窗口对比",
                "excerpt": (
                    f"稳定窗口 {baseline['count']} 批，平均良率 {baseline['average_yield']}%；"
                    f"异常窗口 {anomaly['count']} 批，平均良率 {anomaly['average_yield']}%；"
                    f"变化 {analysis['yield_change_pp']} 个百分点。"
                ),
                "source_ref": "analytics/production-window-v1",
                "score": 0.99 if analysis["evidence_quality"]["sufficient"] else 0.55,
            },
            {
                "id": "STAT-CORR-1",
                "source_type": "analytics",
                "title": "过程参数与缺陷率相关性",
                "excerpt": correlation_text,
                "source_ref": "analytics/pearson-v1",
                "score": 0.96 if analysis["evidence_quality"]["sufficient"] else 0.52,
            },
            {
                "id": "STAT-TIME-1",
                "source_type": "analytics",
                "title": "告警与异常批次时间重合",
                "excerpt": (
                    f"±{overlap['window_hours']}小时窗口内匹配 {overlap['matched_alarm_count']} 条告警；"
                    f"最近时间差 {overlap['closest_delta_minutes']} 分钟。"
                    if overlap["overlap"]
                    else f"±{overlap['window_hours']}小时窗口内未发现可匹配告警。"
                ),
                "source_ref": "analytics/alarm-overlap-v1",
                "score": 0.95 if overlap["overlap"] else 0.6,
            },
        ]
        detail = (
            f"基线 {baseline['average_yield']}% → 异常 {anomaly['average_yield']}%，"
            f"下降 {abs(analysis['yield_change_pp'] or 0):.2f} 个百分点；"
            f"告警时间{'重合' if overlap['overlap'] else '未重合'}"
        )
        self._event(
            state,
            "statistical_analysis",
            "确定性生产统计分析",
            "completed",
            detail,
            analysis,
        )
        return {"production_analysis": analysis, "evidence": evidence}

    def root_cause_analysis(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "rca", "根因分析", "running", "RCAAgent 正在构建跨数据源证据链并校验置信度")
        hypotheses = self.rca_engine.analyze(state)
        degraded = bool(hypotheses and hypotheses[0].get("degraded"))
        if degraded:
            detail = (
                "外部模型暂时不可用，已降级为确定性证据规则；"
                f"形成 {len(hypotheses)} 个根因假设，处置前仍需人工审批"
            )
        else:
            detail = (
                f"形成 {len(hypotheses)} 个有据可依的根因假设，"
                f"首要假设置信度 {hypotheses[0]['confidence']:.0%}"
            )
        self._event(
            state,
            "rca",
            "根因分析",
            "completed",
            detail,
            {"hypotheses": hypotheses, "degraded": degraded},
        )
        return {"hypotheses": hypotheses}

    def create_action_plan(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "plan", "处置方案与风险评估", "running", "ActionAgent 正在生成最小权限处置计划")
        plan = [
            {
                "tool": "action-mcp.create_maintenance_order",
                "title": "创建 P1 光学组件检查工单",
                "description": "停机并执行能源隔离后，检查/清洁保护镜片，校准焦距与激光功率。",
                "risk": "medium",
                "parameters": {"device_id": state["device_id"], "priority": "P1"},
            },
            {
                "tool": "action-mcp.tighten_inspection",
                "title": "将临时抽检周期调整为 10 分钟",
                "description": "仅修改模拟 MES 线控参数；连续三批良率达到 97% 后恢复。",
                "risk": "high",
                "parameters": {"line_id": state["line_id"], "interval_minutes": 10},
            },
        ]
        risk_level = "high"
        self.database.update_run(state["run_id"], risk_level=risk_level)
        self._event(state, "plan", "处置方案与风险评估", "completed", "包含 2 个受控写操作，综合风险 high，必须人工审批", {"risk_level": risk_level, "actions": plan})
        return {"action_plan": plan, "risk_level": risk_level}

    def request_approval(self, state: AgentState) -> Command:
        self.database.update_run(state["run_id"], status="awaiting_approval")
        self._event(state, "approval", "等待人工审批", "waiting", "工作流已持久化暂停；拒绝时不会执行任何写操作", {"risk_level": state["risk_level"], "actions": state["action_plan"]})
        decision = interrupt(
            {
                "run_id": state["run_id"],
                "question": "是否批准执行以下模拟 MES 写操作？",
                "risk_level": state["risk_level"],
                "actions": state["action_plan"],
            }
        )
        approval = {
            "approval_id": decision.get("approval_id"),
            "approved": bool(decision.get("approved")),
            "approver": decision.get("approver", ""),
            "comment": decision.get("comment", ""),
            "decided_at": utc_now(),
        }
        self.database.update_run(state["run_id"], approval_json=json.dumps(approval, ensure_ascii=False))
        return Command(update={"approval": approval}, goto="execute" if approval["approved"] else "reject")

    def execute_actions(self, state: AgentState) -> dict[str, Any]:
        self.database.update_run(state["run_id"], status="executing")
        self._event(state, "execute", "执行受控业务工具", "running", "审批已验证，action-mcp 按白名单参数执行")
        approval = state["approval"]
        order = self.actions.create_maintenance_order(
            state["run_id"],
            state["device_id"],
            "P1",
            "检查 2号线激光打码机光学组件",
            "检查并清洁保护镜片，校准焦距与激光功率；使用标准样件验证后记录结果。",
            approval,
        )
        inspection = self.actions.tighten_inspection(
            state["run_id"], state["line_id"], 10, approval
        )
        results = [order, inspection]
        self._event(state, "execute", "执行受控业务工具", "completed", f"已创建工单 {order['work_order_id']}，抽检周期已调整为 10 分钟", {"results": results})
        return {"action_results": results}

    def reject_actions(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "execute", "执行受控业务工具", "skipped", "审批被拒绝，未调用任何写工具", {"approval": state["approval"]})
        return {"action_results": []}

    def verify_result(self, state: AgentState) -> dict[str, Any]:
        self._event(state, "verify", "处理结果验证", "running", "读取模拟工单与线控参数，并生成三批验证窗口")
        order = self.database.fetch_one("SELECT * FROM work_orders WHERE run_id=? ORDER BY created_at DESC", (state["run_id"],))
        control = self.database.fetch_one("SELECT * FROM line_controls WHERE line_id=?", (state["line_id"],))
        verification_batches = [96.8, 97.4, 97.9]
        recovery = evaluate_recovery(verification_batches)
        verification = {
            "mode": "simulation",
            "write_state_verified": bool(order and control and control["inspection_interval_minutes"] == 10),
            "baseline_yield": state.get("production_analysis", {}).get("anomaly", {}).get("average_yield", 91.0),
            "verification_batches": verification_batches,
            "release_condition_met": recovery["release_condition_met"],
            "note": "仅 2/3 批达到 97%，继续加严检验；此结果为演示模拟，不代表真实产线。",
        }
        production_analysis = dict(state.get("production_analysis", {}))
        production_analysis["recovery_validation"] = recovery
        self._event(state, "verify", "处理结果验证", "completed", verification["note"], verification)
        return {"verification": verification, "production_analysis": production_analysis}

    def generate_report(self, state: AgentState) -> dict[str, Any]:
        rejected = not state.get("approval", {}).get("approved")
        report = {
            "report_id": f"IR-{state['run_id'][:8].upper()}",
            "generated_at": utc_now(),
            "incident": state["query"],
            "scope": {"line_id": state["line_id"], "device_id": state["device_id"]},
            "severity": state["severity"],
            "primary_cause": state.get("hypotheses", [{}])[0],
            "evidence_count": len(state.get("evidence", [])),
            "approval": state.get("approval"),
            "actions": state.get("action_results", []),
            "verification": state.get("verification"),
            "production_analysis": state.get("production_analysis"),
            "conclusion": "人工拒绝，工作流安全终止，未产生写操作。" if rejected else "处置已执行，继续观察直至连续三批满足放行条件。",
            "data_notice": "本报告由 FactoryPilot 使用模拟 MES 数据生成。",
        }
        self.database.update_run(
            state["run_id"],
            report_json=json.dumps(report, ensure_ascii=False),
        )
        self._event(state, "report", "事故报告", "completed", report["conclusion"], {"report_id": report["report_id"]})
        return {"report": report}
