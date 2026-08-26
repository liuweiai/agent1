from __future__ import annotations

from datetime import datetime
from math import sqrt
from statistics import mean
from typing import Any


QUALITY_RELEASE_THRESHOLD = 97.0
ALARM_OVERLAP_HOURS = 6


def _pearson(rows: list[dict[str, Any]], x_key: str, y_key: str) -> float | None:
    pairs = [
        (float(row[x_key]), float(row[y_key]))
        for row in rows
        if row.get(x_key) is not None and row.get(y_key) is not None
    ]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    x_mean, y_mean = mean(xs), mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    denominator = sqrt(
        sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys)
    )
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def _correlation_label(value: float | None) -> str:
    if value is None:
        return "样本不足"
    strength = "强" if abs(value) >= 0.7 else "中等" if abs(value) >= 0.4 else "弱"
    direction = "正相关" if value > 0 else "负相关" if value < 0 else "无相关"
    return f"{strength}{direction}"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def analyze_production_window(
    quality: list[dict[str, Any]],
    alarms: list[dict[str, Any]],
    release_threshold: float = QUALITY_RELEASE_THRESHOLD,
    overlap_hours: int = ALARM_OVERLAP_HOURS,
) -> dict[str, Any]:
    """Create deterministic statistical evidence before any LLM reasoning."""
    chronological = sorted(quality, key=lambda row: row.get("started_at", ""))
    trend = []
    for row in chronological:
        yield_rate = float(row["yield_rate"])
        trend.append(
            {
                "batch_id": row["batch_id"],
                "started_at": row["started_at"],
                "yield_rate": round(yield_rate, 2),
                "defect_rate": round(100.0 - yield_rate, 2),
                "marking_speed": row.get("marking_speed"),
                "laser_power_percent": row.get("laser_power_percent"),
                "temperature_c": row.get("temperature_c"),
                "defect_type": row.get("defect_type"),
            }
        )

    baseline_rows = [row for row in trend if row["yield_rate"] >= release_threshold]
    anomaly_rows = [row for row in trend if row["yield_rate"] < release_threshold]

    def window_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"count": 0, "average_yield": None, "average_defect_rate": None}
        return {
            "count": len(rows),
            "average_yield": round(mean(row["yield_rate"] for row in rows), 2),
            "average_defect_rate": round(mean(row["defect_rate"] for row in rows), 2),
        }

    baseline = window_summary(baseline_rows)
    anomaly = window_summary(anomaly_rows)
    yield_change = (
        round(anomaly["average_yield"] - baseline["average_yield"], 2)
        if baseline["average_yield"] is not None and anomaly["average_yield"] is not None
        else None
    )

    correlations: dict[str, dict[str, Any]] = {}
    for key, label in (
        ("marking_speed", "打码速度 vs 缺陷率"),
        ("laser_power_percent", "激光功率 vs 缺陷率"),
        ("temperature_c", "温度 vs 缺陷率"),
    ):
        coefficient = _pearson(trend, key, "defect_rate")
        correlations[key] = {
            "label": label,
            "coefficient": coefficient,
            "interpretation": _correlation_label(coefficient),
        }

    overlap_pairs: list[dict[str, Any]] = []
    for batch in anomaly_rows:
        batch_time = _parse_timestamp(batch["started_at"])
        for alarm in alarms:
            alarm_time = _parse_timestamp(alarm["occurred_at"])
            delta_minutes = abs((alarm_time - batch_time).total_seconds()) / 60
            if delta_minutes <= overlap_hours * 60:
                overlap_pairs.append(
                    {
                        "batch_id": batch["batch_id"],
                        "alarm_id": alarm["id"],
                        "alarm_code": alarm["code"],
                        "delta_minutes": round(delta_minutes),
                    }
                )
    overlap_pairs.sort(key=lambda item: item["delta_minutes"])
    unique_alarm_ids = {pair["alarm_id"] for pair in overlap_pairs}

    limitations = []
    if len(trend) < 8:
        limitations.append("质量窗口少于8个批次")
    if len(baseline_rows) < 3:
        limitations.append("稳定基线少于3个批次")
    if len(anomaly_rows) < 2:
        limitations.append("异常窗口少于2个批次")
    if not alarms:
        limitations.append("告警工具未返回数据，无法验证时间重合")
    sufficient = len(trend) >= 8 and len(baseline_rows) >= 3 and len(anomaly_rows) >= 2

    return {
        "method": "deterministic-statistics-v1",
        "window_size": len(trend),
        "release_threshold": release_threshold,
        "trend": trend,
        "baseline": baseline,
        "anomaly": anomaly,
        "yield_change_pp": yield_change,
        "correlations": correlations,
        "alarm_overlap": {
            "window_hours": overlap_hours,
            "overlap": bool(overlap_pairs),
            "matched_alarm_count": len(unique_alarm_ids),
            "closest_delta_minutes": overlap_pairs[0]["delta_minutes"] if overlap_pairs else None,
            "pairs": overlap_pairs[:8],
        },
        "evidence_quality": {
            "sufficient": sufficient,
            "limitations": limitations,
        },
        "recovery_validation": {
            "status": "pending",
            "required_consecutive_batches": 3,
            "threshold": release_threshold,
        },
    }


def evaluate_recovery(
    yields: list[float],
    threshold: float = QUALITY_RELEASE_THRESHOLD,
    required_consecutive_batches: int = 3,
) -> dict[str, Any]:
    consecutive = 0
    for value in reversed(yields):
        if value >= threshold:
            consecutive += 1
        else:
            break
    return {
        "status": "passed" if consecutive >= required_consecutive_batches else "observing",
        "threshold": threshold,
        "required_consecutive_batches": required_consecutive_batches,
        "observed_batches": [round(float(value), 2) for value in yields],
        "current_consecutive_batches": consecutive,
        "release_condition_met": consecutive >= required_consecutive_batches,
    }
