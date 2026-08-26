from __future__ import annotations

import json
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import TimelineEvent, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    line_id TEXT NOT NULL,
    name TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    install_date TEXT NOT NULL,
    runtime_hours INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    code TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(device_id) REFERENCES devices(id)
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    line_id TEXT NOT NULL,
    product_code TEXT NOT NULL,
    started_at TEXT NOT NULL,
    planned_qty INTEGER NOT NULL,
    produced_qty INTEGER NOT NULL,
    good_qty INTEGER NOT NULL,
    marking_speed INTEGER NOT NULL,
    material_lot TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_metrics (
    batch_id TEXT PRIMARY KEY,
    laser_power_percent REAL NOT NULL,
    temperature_c REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY(batch_id) REFERENCES batches(id)
);

CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    defect_type TEXT NOT NULL,
    inspected_qty INTEGER NOT NULL,
    defect_qty INTEGER NOT NULL,
    measured_at TEXT NOT NULL,
    FOREIGN KEY(batch_id) REFERENCES batches(id),
    FOREIGN KEY(device_id) REFERENCES devices(id)
);

CREATE TABLE IF NOT EXISTS maintenance_records (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    resolution TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    FOREIGN KEY(device_id) REFERENCES devices(id)
);

CREATE TABLE IF NOT EXISTS knowledge_docs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL,
    version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS line_controls (
    line_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    inspection_interval_minutes INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_orders (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    priority TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    line_id TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT,
    risk_level TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    approval_json TEXT,
    report_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, idempotency_key),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    operation TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_device_time ON alerts(device_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_batches_line_time ON batches(line_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_run_id ON run_events(run_id, id);
"""


class Database:
    """Small SQLite repository used by the self-contained demo profile."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.session() as connection:
            connection.executescript(SCHEMA)
            count = connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            if count == 0:
                self._seed(connection)
            self._ensure_process_metrics(connection)

    def _ensure_process_metrics(self, connection: Any) -> None:
        """Backfill deterministic process parameters for fresh and existing demo data."""
        cursor = connection.execute(
            "SELECT id,started_at,produced_qty,good_qty FROM batches ORDER BY started_at"
        )
        rows = cursor.fetchall()
        cursor.close()
        metrics = []
        for row in rows:
            produced = int(row["produced_qty"])
            defect_rate = 100.0 * (produced - int(row["good_qty"])) / produced
            batch_number = int(str(row["id"]).split("-")[-1])
            jitter = ((batch_number % 7) - 3) * 0.08
            laser_power = max(68.0, min(100.0, 101.2 - defect_rate * 3.0 + jitter))
            temperature = 38.5 + defect_rate * 1.65 + jitter
            metrics.append(
                (
                    row["id"],
                    round(laser_power, 2),
                    round(temperature, 2),
                    row["started_at"],
                )
            )
        connection.executemany(
            "INSERT OR IGNORE INTO process_metrics(batch_id,laser_power_percent,temperature_c,recorded_at) VALUES (?,?,?,?)",
            metrics,
        )

    def _seed(self, connection: sqlite3.Connection) -> None:
        rng = random.Random(20250825)
        now = datetime.now(timezone.utc)

        devices = []
        for index in range(1, 11):
            device_id = f"DEV-L{index:02d}-LASER"
            devices.append(
                (
                    device_id,
                    f"LINE-{index:02d}",
                    f"{index}号产线激光打码机",
                    "LM-5000",
                    "warning" if index == 2 else "running",
                    (now - timedelta(days=900 + index * 20)).date().isoformat(),
                    11800 + index * 137,
                )
            )
        connection.executemany(
            "INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, ?)", devices
        )

        alerts = []
        alert_catalog = [
            ("TEMP-101", "warning", "激光器温度接近上限"),
            ("OPT-207", "warning", "光路能量衰减超过阈值"),
            ("INK-014", "info", "耗材余量偏低"),
            ("NET-003", "warning", "设备网络短时中断"),
            ("POS-112", "warning", "视觉定位偏移"),
        ]
        for index in range(1000):
            device_number = (index % 10) + 1
            device_id = f"DEV-L{device_number:02d}-LASER"
            code, level, message = alert_catalog[index % len(alert_catalog)]
            occurred = now - timedelta(hours=index * 2 + rng.randint(0, 40))
            alerts.append((device_id, code, level, message, occurred.isoformat(), index % 3 == 0))
        # Make the demo incident evidence explicit and recent.
        for hours, code, message in [
            (1, "OPT-207", "光路能量衰减 18%，超过 10% 阈值"),
            (2, "POS-112", "视觉定位连续偏移 0.7mm"),
            (4, "OPT-207", "激光功率反馈波动"),
        ]:
            alerts.append(("DEV-L02-LASER", code, "warning", message, (now - timedelta(hours=hours)).isoformat(), 0))
        connection.executemany(
            "INSERT INTO alerts(device_id,code,level,message,occurred_at,acknowledged) VALUES (?,?,?,?,?,?)",
            alerts,
        )

        batches = []
        inspections = []
        defect_types = ["none", "marking_blur", "position_offset", "missing_code"]
        for index in range(500):
            line_number = (index % 10) + 1
            line_id = f"LINE-{line_number:02d}"
            batch_id = f"BATCH-{5000 + index}"
            started = now - timedelta(hours=index * 3)
            planned = 1000
            produced = 920 + rng.randint(0, 80)
            # The latest 2号线 batches show the requested 91% yield decline.
            anomalous = line_number == 2 and index < 32
            yield_rate = rng.uniform(0.902, 0.918) if anomalous else rng.uniform(0.972, 0.992)
            good = int(produced * yield_rate)
            speed = 135 if anomalous else 120
            batches.append(
                (batch_id, line_id, "HOUSING-A17", started.isoformat(), planned, produced, good, speed, f"MAT-{index % 17:03d}")
            )
            defects = produced - good
            inspections.append(
                (
                    batch_id,
                    f"DEV-L{line_number:02d}-LASER",
                    defect_types[(index % 3) + 1] if anomalous else defect_types[index % 4],
                    produced,
                    defects,
                    (started + timedelta(minutes=45)).isoformat(),
                )
            )
        connection.executemany("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)", batches)
        connection.executemany(
            "INSERT INTO inspections(batch_id,device_id,defect_type,inspected_qty,defect_qty,measured_at) VALUES (?,?,?,?,?,?)",
            inspections,
        )

        maintenance = []
        resolutions = [
            ("preventive", "月度点检", "清洁镜片并校准焦距"),
            ("optics", "打码模糊、能量衰减", "更换保护镜片并执行功率校准"),
            ("position", "定位偏移", "重新标定视觉基准点"),
            ("network", "设备通信中断", "更换交换机端口并固定网线"),
        ]
        for index in range(100):
            device_number = (index % 10) + 1
            category, description, resolution = resolutions[index % len(resolutions)]
            maintenance.append(
                (
                    f"MR-{3000 + index}",
                    f"DEV-L{device_number:02d}-LASER",
                    category,
                    description,
                    resolution,
                    (now - timedelta(days=index * 4 + 3)).isoformat(),
                )
            )
        # Relevant record: the optics cleaning interval is overdue.
        maintenance.append(
            (
                "MR-DEMO-002",
                "DEV-L02-LASER",
                "optics",
                "曾发生相同的打码模糊和功率波动",
                "清洁保护镜片后良率由 92.1% 恢复到 98.3%",
                (now - timedelta(days=95)).isoformat(),
            )
        )
        connection.executemany("INSERT INTO maintenance_records VALUES (?,?,?,?,?,?)", maintenance)

        base_docs = [
            (
                "SOP-LASER-007",
                "激光打码质量异常处置 SOP",
                "3.2 打码模糊与能量衰减",
                "当光路能量衰减超过10%或打码模糊缺陷连续出现时，应暂停放行，检查保护镜片污染、焦距和激光功率。清洁镜片后使用标准样件验证，连续三批良率达到97%方可恢复正常放行。",
                "laser,optics,quality,marking_blur",
                "v2.1",
            ),
            (
                "MANUAL-LM5000-12",
                "LM-5000 激光打码机维护手册",
                "12.4 光学组件保养",
                "保护镜片建议每30天检查一次。粉尘环境下能量衰减达到10%应立即清洁；清洁前必须停机并执行能源隔离，禁止在激光器工作状态打开光路组件。",
                "LM-5000,safety,optics,maintenance",
                "v4.3",
            ),
            (
                "SOP-QA-015",
                "制程质量加严检验规范",
                "2.1 临时加严检验",
                "制程良率下降超过3个百分点时，将抽检周期由30分钟缩短至10分钟。异常关闭条件为连续三批良率不低于97%，且关键缺陷无重复发生。",
                "quality,inspection,yield",
                "v1.8",
            ),
            (
                "SOP-MES-004",
                "MES 异常停线与恢复规范",
                "4.1 审批与审计",
                "暂停产线、修改生产参数和恢复生产均属于受控操作，必须记录操作人、审批人、原因、参数变更前后值和验证结果。未经审批不得由自动化系统直接执行。",
                "MES,approval,audit,safety",
                "v3.0",
            ),
        ]
        docs = list(base_docs)
        for index in range(4, 30):
            docs.append(
                (
                    f"DOC-{index:03d}",
                    f"设备运维知识条目 {index:02d}",
                    "常见故障",
                    f"示例知识条目 {index}：记录温度、网络、耗材或定位异常的检查步骤，执行任何写操作前必须确认设备与批次范围。",
                    "maintenance,example",
                    "v1.0",
                )
            )
        connection.executemany("INSERT INTO knowledge_docs VALUES (?,?,?,?,?,?)", docs)

        connection.executemany(
            "INSERT INTO line_controls VALUES (?, ?, ?, ?)",
            [(f"LINE-{index:02d}", "running", 30, utc_now()) for index in range(1, 11)],
        )

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        with self.session() as connection:
            connection.execute(sql, parameters)

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(sql, parameters).fetchall()
            return [dict(row) for row in rows]

    def create_run(self, run_id: str, query: str, line_id: str) -> None:
        now = utc_now()
        self.execute(
            "INSERT INTO runs(id,query,line_id,status,state_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, query, line_id, "queued", "{}", now, now),
        )

    def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "severity",
            "risk_level",
            "state_json",
            "approval_json",
            "report_json",
            "error",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in updates)
        self.execute(
            f"UPDATE runs SET {assignments} WHERE id=?",  # columns come from fixed allowlist
            tuple(updates.values()) + (run_id,),
        )

    def emit_event(
        self,
        run_id: str,
        step: str,
        title: str,
        status: str,
        detail: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        key = idempotency_key or f"{step}:{status}"
        with self.session() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO run_events(
                    run_id,step,title,status,detail,payload_json,idempotency_key,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (run_id, step, title, status, detail, json.dumps(payload or {}, ensure_ascii=False), key, utc_now()),
            )

    def get_events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            "SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id", (run_id, after_id)
        )
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not row:
            return None
        for output_key, db_key in [
            ("state", "state_json"),
            ("approval", "approval_json"),
            ("report", "report_json"),
        ]:
            raw = row.pop(db_key)
            row[output_key] = json.loads(raw) if raw else None
        row["events"] = self.get_events(run_id)
        return row

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT id,query,line_id,status,severity,risk_level,created_at,updated_at FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    def audit(
        self,
        run_id: str,
        actor: str,
        tool: str,
        operation: str,
        parameters: dict[str, Any],
        outcome: str,
        duration_ms: int,
    ) -> None:
        self.execute(
            """
            INSERT INTO audit_logs(run_id,actor,tool,operation,parameters_json,outcome,duration_ms,created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (run_id, actor, tool, operation, json.dumps(parameters, ensure_ascii=False), outcome, duration_ms, utc_now()),
        )
