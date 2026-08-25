from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator
from urllib.parse import urlparse, unquote

import pymysql
from pymysql.cursors import DictCursor

from .database import Database


MYSQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id VARCHAR(64) PRIMARY KEY, line_id VARCHAR(32) NOT NULL, name VARCHAR(128) NOT NULL,
    model VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, install_date VARCHAR(40) NOT NULL,
    runtime_hours INT NOT NULL, INDEX idx_devices_line(line_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS alerts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT, device_id VARCHAR(64) NOT NULL, code VARCHAR(32) NOT NULL,
    level VARCHAR(24) NOT NULL, message VARCHAR(500) NOT NULL, occurred_at VARCHAR(40) NOT NULL,
    acknowledged TINYINT NOT NULL DEFAULT 0,
    INDEX idx_alerts_device_time(device_id, occurred_at),
    CONSTRAINT fk_alert_device FOREIGN KEY(device_id) REFERENCES devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS batches (
    id VARCHAR(64) PRIMARY KEY, line_id VARCHAR(32) NOT NULL, product_code VARCHAR(64) NOT NULL,
    started_at VARCHAR(40) NOT NULL, planned_qty INT NOT NULL, produced_qty INT NOT NULL,
    good_qty INT NOT NULL, marking_speed INT NOT NULL, material_lot VARCHAR(64) NOT NULL,
    INDEX idx_batches_line_time(line_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS inspections (
    id BIGINT PRIMARY KEY AUTO_INCREMENT, batch_id VARCHAR(64) NOT NULL, device_id VARCHAR(64) NOT NULL,
    defect_type VARCHAR(64) NOT NULL, inspected_qty INT NOT NULL, defect_qty INT NOT NULL,
    measured_at VARCHAR(40) NOT NULL,
    CONSTRAINT fk_inspection_batch FOREIGN KEY(batch_id) REFERENCES batches(id),
    CONSTRAINT fk_inspection_device FOREIGN KEY(device_id) REFERENCES devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS maintenance_records (
    id VARCHAR(64) PRIMARY KEY, device_id VARCHAR(64) NOT NULL, category VARCHAR(64) NOT NULL,
    description VARCHAR(1000) NOT NULL, resolution VARCHAR(1000) NOT NULL, completed_at VARCHAR(40) NOT NULL,
    INDEX idx_maintenance_device(device_id),
    CONSTRAINT fk_maintenance_device FOREIGN KEY(device_id) REFERENCES devices(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id VARCHAR(64) PRIMARY KEY, title VARCHAR(255) NOT NULL, section VARCHAR(255) NOT NULL,
    content TEXT NOT NULL, tags VARCHAR(500) NOT NULL, version VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS line_controls (
    line_id VARCHAR(32) PRIMARY KEY, status VARCHAR(32) NOT NULL,
    inspection_interval_minutes INT NOT NULL, updated_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS work_orders (
    id VARCHAR(64) PRIMARY KEY, run_id CHAR(32) NOT NULL, device_id VARCHAR(64) NOT NULL,
    priority VARCHAR(8) NOT NULL, title VARCHAR(255) NOT NULL, description TEXT NOT NULL,
    status VARCHAR(32) NOT NULL, created_at VARCHAR(40) NOT NULL, INDEX idx_work_orders_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS runs (
    id CHAR(32) PRIMARY KEY, query VARCHAR(1000) NOT NULL, line_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL, severity VARCHAR(32), risk_level VARCHAR(32), state_json JSON,
    approval_json JSON, report_json JSON, error TEXT, created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL, INDEX idx_runs_created(created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS run_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT, run_id CHAR(32) NOT NULL, step VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL, status VARCHAR(32) NOT NULL, detail TEXT NOT NULL,
    payload_json JSON NOT NULL, idempotency_key VARCHAR(128) NOT NULL, created_at VARCHAR(40) NOT NULL,
    UNIQUE KEY uk_run_event(run_id, idempotency_key), INDEX idx_events_run_id(run_id, id),
    CONSTRAINT fk_event_run FOREIGN KEY(run_id) REFERENCES runs(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT, run_id VARCHAR(64) NOT NULL, actor VARCHAR(128) NOT NULL,
    tool VARCHAR(64) NOT NULL, operation VARCHAR(128) NOT NULL, parameters_json JSON NOT NULL,
    outcome VARCHAR(255) NOT NULL, duration_ms INT NOT NULL, created_at VARCHAR(40) NOT NULL,
    INDEX idx_audit_run(run_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


class MySQLConnectionAdapter:
    def __init__(self, connection: pymysql.Connection):
        self.connection = connection

    @staticmethod
    def _sql(sql: str) -> str:
        converted = sql.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO")
        return converted.replace("?", "%s")

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()):
        cursor = self.connection.cursor()
        cursor.execute(self._sql(sql), parameters)
        return cursor

    def executemany(self, sql: str, parameters: list[tuple[Any, ...]]) -> None:
        cursor = self.connection.cursor()
        cursor.executemany(self._sql(sql), parameters)
        cursor.close()

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class MySQLDatabase(Database):
    """MySQL implementation preserving the repository's parameterized-query contract."""

    def __init__(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme not in {"mysql", "mysql+pymysql"}:
            raise ValueError("DATABASE_URL 必须使用 mysql:// 或 mysql+pymysql://")
        self.connection_options = {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or "factorypilot"),
            "password": unquote(parsed.password or ""),
            "database": parsed.path.lstrip("/") or "factorypilot",
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "connect_timeout": 10,
            "read_timeout": 15,
            "write_timeout": 15,
            "autocommit": False,
        }
        self.path = url

    def connect(self) -> MySQLConnectionAdapter:
        return MySQLConnectionAdapter(pymysql.connect(**self.connection_options))

    @staticmethod
    def _normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """Keep the repository contract JSON-safe across SQLite and MySQL drivers."""
        if row is None:
            return None
        return {
            key: float(value) if isinstance(value, Decimal) else value
            for key, value in row.items()
        }

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return self._normalize_row(super().fetch_one(sql, parameters))

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [self._normalize_row(row) or {} for row in super().fetch_all(sql, parameters)]

    @contextmanager
    def session(self) -> Iterator[MySQLConnectionAdapter]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        statements = [statement.strip() for statement in MYSQL_SCHEMA.split(";") if statement.strip()]
        with self.session() as connection:
            for statement in statements:
                cursor = connection.execute(statement)
                cursor.close()
            cursor = connection.execute("SELECT COUNT(*) AS count FROM devices")
            row = cursor.fetchone()
            cursor.close()
            if int(row["count"]) == 0:
                self._seed(connection)  # Database._seed uses only execute/executemany here.


def create_database(settings: Any) -> Database:
    if settings.data_backend == "mysql":
        return MySQLDatabase(settings.database_url)
    return Database(settings.database_path)
