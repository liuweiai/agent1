from __future__ import annotations

import time
from typing import Any

from neo4j import GraphDatabase, RoutingControl

from .config import Settings
from .database import Database


FAULT_PATHS = [
    {
        "model": "LM-5000",
        "component": "保护镜片",
        "failure": "镜片污染",
        "symptom": "光路能量衰减、打码模糊、良率下降",
        "remedy": "停机并执行能源隔离，清洁或更换保护镜片，随后校准功率",
        "sop_id": "SOP-LASER-007",
        "section": "3.2 打码模糊与能量衰减",
        "risk": "high",
    },
    {
        "model": "LM-5000",
        "component": "激光电源",
        "failure": "功率反馈漂移",
        "symptom": "激光功率波动、字符深浅不一致",
        "remedy": "检查功率反馈和电源输出，使用标准样件重新标定",
        "sop_id": "MANUAL-LM5000-12",
        "section": "12.4 光学组件保养",
        "risk": "medium",
    },
    {
        "model": "LM-5000",
        "component": "视觉定位模块",
        "failure": "基准点漂移",
        "symptom": "打码位置偏移、视觉定位告警",
        "remedy": "复核治具并重新标定视觉基准点",
        "sop_id": "SOP-LASER-007",
        "section": "4.1 定位偏移",
        "risk": "medium",
    },
    {
        "model": "LM-5000",
        "component": "冷却系统",
        "failure": "散热能力下降",
        "symptom": "设备温度升高、连续运行后缺陷率增加",
        "remedy": "检查冷却液、风道和过滤网，恢复后进行温升验证",
        "sop_id": "MANUAL-LM5000-12",
        "section": "9.3 冷却系统",
        "risk": "high",
    },
    {
        "model": "LM-5000",
        "component": "通信模块",
        "failure": "工业网络抖动",
        "symptom": "设备离线、任务下发超时、数据采集中断",
        "remedy": "检查交换机端口、网线和设备心跳，禁止重复下发已确认任务",
        "sop_id": "SOP-MES-004",
        "section": "2.3 通信异常",
        "risk": "medium",
    },
    {
        "model": "LM-5000",
        "component": "耗材模块",
        "failure": "保护气体或耗材不足",
        "symptom": "打码对比度下降、耗材告警",
        "remedy": "核对耗材批次和余量，更换后使用标准样件验证",
        "sop_id": "SOP-LASER-007",
        "section": "5.2 耗材检查",
        "risk": "low",
    },
]


class DisabledGraphKnowledgeTools:
    enabled = False

    def search_fault_paths(
        self, run_id: str, model: str, query: str, limit: int = 4
    ) -> list[dict[str, Any]]:
        return []

    def health(self) -> bool:
        return False

    def close(self) -> None:
        return None


class Neo4jFaultKnowledgeTools:
    """Read-only fault-path traversal backed by a seeded Neo4j knowledge graph."""

    enabled = True

    def __init__(
        self,
        database: Database,
        settings: Settings,
        driver: Any | None = None,
    ):
        self.database = database
        self.settings = settings
        self.driver = driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
            connection_timeout=5,
        )
        try:
            self.driver.verify_connectivity()
            self.initialize()
        except Exception:
            self.driver.close()
            if not settings.neo4j_fail_open:
                raise
            self.enabled = False

    def initialize(self) -> None:
        constraints = [
            "CREATE CONSTRAINT device_model_id IF NOT EXISTS FOR (n:DeviceModel) REQUIRE n.model IS UNIQUE",
            "CREATE CONSTRAINT component_name IF NOT EXISTS FOR (n:Component) REQUIRE n.name IS UNIQUE",
            "CREATE CONSTRAINT failure_code IF NOT EXISTS FOR (n:FailureMode) REQUIRE n.code IS UNIQUE",
            "CREATE CONSTRAINT symptom_name IF NOT EXISTS FOR (n:Symptom) REQUIRE n.name IS UNIQUE",
            "CREATE CONSTRAINT remedy_name IF NOT EXISTS FOR (n:Remedy) REQUIRE n.name IS UNIQUE",
            "CREATE CONSTRAINT sop_id IF NOT EXISTS FOR (n:SOP) REQUIRE n.id IS UNIQUE",
        ]
        for statement in constraints:
            self.driver.execute_query(
                statement, database_=self.settings.neo4j_database
            )
        rows = []
        for index, path in enumerate(FAULT_PATHS, start=1):
            rows.append({**path, "failure_code": f"FM-LM5000-{index:03d}"})
        self.driver.execute_query(
            """
            UNWIND $rows AS row
            MERGE (model:DeviceModel {model: row.model})
            MERGE (component:Component {name: row.component})
            MERGE (failure:FailureMode {code: row.failure_code})
              SET failure.name = row.failure, failure.risk = row.risk
            MERGE (symptom:Symptom {name: row.symptom})
            MERGE (remedy:Remedy {name: row.remedy})
            MERGE (sop:SOP {id: row.sop_id})
              SET sop.section = row.section
            MERGE (model)-[:HAS_COMPONENT]->(component)
            MERGE (failure)-[:AFFECTS]->(component)
            MERGE (failure)-[:INDICATED_BY]->(symptom)
            MERGE (failure)-[:MITIGATED_BY]->(remedy)
            MERGE (remedy)-[:DOCUMENTED_IN]->(sop)
            """,
            rows=rows,
            database_=self.settings.neo4j_database,
        )

    @staticmethod
    def keywords(query: str) -> list[str]:
        dictionary = [
            "良率",
            "打码",
            "模糊",
            "偏移",
            "功率",
            "能量",
            "镜片",
            "温度",
            "网络",
            "离线",
            "耗材",
        ]
        return [word for word in dictionary if word in query]

    def search_fault_paths(
        self, run_id: str, model: str, query: str, limit: int = 4
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        if not 1 <= limit <= 8:
            raise ValueError("Neo4j fault path limit 必须在 1..8")
        started = time.perf_counter()
        keywords = self.keywords(query)
        try:
            records, _, _ = self.driver.execute_query(
                """
                MATCH (model:DeviceModel {model: $model})-[:HAS_COMPONENT]->(component:Component)
                MATCH (failure:FailureMode)-[:AFFECTS]->(component)
                MATCH (failure)-[:INDICATED_BY]->(symptom:Symptom)
                MATCH (failure)-[:MITIGATED_BY]->(remedy:Remedy)-[:DOCUMENTED_IN]->(sop:SOP)
                WITH component, failure, symptom, remedy, sop,
                     size([keyword IN $keywords WHERE
                         toLower(symptom.name) CONTAINS toLower(keyword) OR
                         toLower(failure.name) CONTAINS toLower(keyword) OR
                         toLower(component.name) CONTAINS toLower(keyword)]) AS matches
                WHERE size($keywords) = 0 OR matches > 0
                RETURN failure.code AS path_id, component.name AS component,
                       failure.name AS failure, failure.risk AS risk,
                       symptom.name AS symptom, remedy.name AS remedy,
                       sop.id AS sop_id, sop.section AS section, matches
                ORDER BY matches DESC, path_id ASC
                LIMIT $limit
                """,
                model=model,
                keywords=keywords,
                limit=limit,
                routing_=RoutingControl.READ,
                database_=self.settings.neo4j_database,
            )
            result = [record.data() for record in records]
            self.database.audit(
                run_id,
                "RCAAgent",
                "neo4j-graph",
                "traverse_fault_paths",
                {"model": model, "keywords": keywords, "limit": limit},
                f"success:{len(result)}",
                int((time.perf_counter() - started) * 1000),
            )
            return result
        except Exception as exc:
            self.database.audit(
                run_id,
                "RCAAgent",
                "neo4j-graph",
                "traverse_fault_paths",
                {"model": model, "keywords": keywords, "limit": limit},
                f"failed:{type(exc).__name__}",
                int((time.perf_counter() - started) * 1000),
            )
            if not self.settings.neo4j_fail_open:
                raise
            return []

    def health(self) -> bool:
        if not self.enabled:
            return False
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.driver.close()


def create_graph_knowledge_tools(
    database: Database, settings: Settings
) -> DisabledGraphKnowledgeTools | Neo4jFaultKnowledgeTools:
    if not settings.neo4j_enabled:
        return DisabledGraphKnowledgeTools()
    tools = Neo4jFaultKnowledgeTools(database, settings)
    if not tools.enabled:
        return DisabledGraphKnowledgeTools()
    return tools
