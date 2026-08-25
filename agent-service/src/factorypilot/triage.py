from __future__ import annotations


def classify_anomaly(query: str) -> tuple[str, str]:
    """Small, testable triage policy used before expensive model/tool calls."""
    quality_tokens = ("良率", "不良", "缺陷", "漏码", "模糊", "偏移", "质量")
    anomaly_type = (
        "quality_yield_drop" if any(token in query for token in quality_tokens) else "equipment_anomaly"
    )
    critical_tokens = ("91%", "90%", "停线", "大幅", "批量", "安全联锁")
    severity = "critical" if any(token in query for token in critical_tokens) else "major"
    return anomaly_type, severity
