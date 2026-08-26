from __future__ import annotations

from datetime import datetime, timedelta, timezone

from factorypilot.production_analytics import analyze_production_window, evaluate_recovery


def _quality_window() -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for index in range(20):
        anomalous = index >= 16
        yield_rate = 91.0 + (index % 3) * 0.2 if anomalous else 98.0 + (index % 4) * 0.1
        defect_rate = 100 - yield_rate
        rows.append(
            {
                "batch_id": f"B-{index:02d}",
                "started_at": (now - timedelta(hours=20 - index)).isoformat(),
                "yield_rate": yield_rate,
                "marking_speed": 135 if anomalous else 120,
                "laser_power_percent": 101.2 - defect_rate * 3,
                "temperature_c": 38.5 + defect_rate * 1.65,
                "defect_type": "marking_blur" if anomalous else "none",
            }
        )
    return rows


def test_production_analysis_calculates_windows_correlations_and_overlap() -> None:
    quality = _quality_window()
    anomaly_time = datetime.fromisoformat(quality[-1]["started_at"])
    alarms = [
        {
            "id": 7,
            "code": "OPT-207",
            "occurred_at": (anomaly_time + timedelta(minutes=20)).isoformat(),
        }
    ]

    analysis = analyze_production_window(quality, alarms)

    assert analysis["window_size"] == 20
    assert analysis["baseline"]["count"] == 16
    assert analysis["anomaly"]["count"] == 4
    assert analysis["yield_change_pp"] < -6
    assert analysis["correlations"]["marking_speed"]["coefficient"] > 0.9
    assert analysis["correlations"]["laser_power_percent"]["coefficient"] < -0.9
    assert analysis["correlations"]["temperature_c"]["coefficient"] > 0.9
    assert analysis["alarm_overlap"]["overlap"] is True
    assert analysis["evidence_quality"]["sufficient"] is True


def test_production_analysis_marks_missing_data_as_insufficient() -> None:
    analysis = analyze_production_window(_quality_window()[:4], [])
    assert analysis["evidence_quality"]["sufficient"] is False
    assert "质量窗口少于8个批次" in analysis["evidence_quality"]["limitations"]
    assert "告警工具未返回数据，无法验证时间重合" in analysis["evidence_quality"]["limitations"]


def test_recovery_requires_three_consecutive_batches() -> None:
    observing = evaluate_recovery([96.8, 97.4, 97.9])
    passed = evaluate_recovery([96.8, 97.1, 97.4, 97.9])
    assert observing["current_consecutive_batches"] == 2
    assert observing["release_condition_met"] is False
    assert passed["current_consecutive_batches"] == 3
    assert passed["release_condition_met"] is True
