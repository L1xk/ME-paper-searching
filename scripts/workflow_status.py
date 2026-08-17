"""Record a public failure heartbeat when the main literature pipeline never ran."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


path = Path("data/automation_status.json")
try:
    current = json.loads(path.read_text(encoding="utf-8-sig"))
except (OSError, json.JSONDecodeError):
    current = {"version": 1, "last_check": None, "last_success": None}

try:
    local_timezone = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    local_timezone = timezone(timedelta(hours=8), "Asia/Shanghai")
now = datetime.now(local_timezone)
iso = now.date().isocalendar()
current = {
    "version": 1,
    "last_check": {
        "checked_at": now.isoformat(),
        "issue_date": now.date().isoformat(),
        "period": f"{iso.year}-W{iso.week:02d}",
        "mode": os.getenv("RUN_MODE", ""),
        "outcome": "workflow_failed_before_pipeline_completion",
        "trigger": os.getenv("GITHUB_EVENT_NAME", ""),
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
    },
    "last_success": current.get("last_success") if isinstance(current, dict) else None,
}
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
