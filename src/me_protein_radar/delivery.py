from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .io_utils import RadarError, load_json, write_json_atomic


def delivery_period(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def load_delivery_state(path: Path) -> dict[str, Any]:
    value = load_json(path, {"version": 1, "deliveries": {}})
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("deliveries"), dict):
        raise RadarError(f"Invalid delivery state: {path}")
    return value


def was_delivered(state: dict[str, Any], period: str) -> bool:
    entry = state.get("deliveries", {}).get(period)
    return isinstance(entry, dict) and entry.get("status") == "sent"


def history_delivery_records(history: dict[str, Any], period: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    recommended = history.get("recommended", {}) if isinstance(history, dict) else {}
    if not isinstance(recommended, dict):
        return records
    for entry in recommended.values():
        if not isinstance(entry, dict):
            continue
        try:
            issue_date = date.fromisoformat(str(entry.get("issue_date", "")))
        except ValueError:
            continue
        if delivery_period(issue_date) == period:
            records.append(entry)
    return records


def mark_delivered(
    state: dict[str, Any],
    period: str,
    *,
    issue_date: str,
    sent_at: str,
    formal_count: int,
    review_count: int,
    subject: str,
    source: str = "smtp",
) -> dict[str, Any]:
    result = {"version": 1, "deliveries": dict(state.get("deliveries", {}))}
    result["deliveries"][period] = {
        "status": "sent",
        "issue_date": issue_date,
        "sent_at": sent_at,
        "formal_count": formal_count,
        "review_count": review_count,
        "subject": subject,
        "source": source,
    }
    return result


def update_automation_status(
    path: Path,
    *,
    checked_at: str,
    issue_date: str,
    period: str,
    mode: str,
    outcome: str,
    trigger: str = "",
    run_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    value = load_json(path, {"version": 1, "last_check": None, "last_success": None})
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RadarError(f"Invalid automation status: {path}")
    record: dict[str, Any] = {
        "checked_at": checked_at,
        "issue_date": issue_date,
        "period": period,
        "mode": mode,
        "outcome": outcome,
        "trigger": trigger,
        "run_id": run_id,
    }
    record.update(details or {})
    result = {
        "version": 1,
        "last_check": record,
        "last_success": value.get("last_success"),
    }
    if outcome == "sent" and mode == "production":
        result["last_success"] = record
    write_json_atomic(path, result)
