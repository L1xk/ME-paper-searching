from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import RadarConfig
from .deepseek import BudgetLedger, DeepSeekClient, screen_all, summarize_selected
from .discovery import discover
from .io_utils import RadarError, assess_journal, load_json, write_json_atomic
from .mailer import send_alert, send_html
from .render import render, subject
from .selection import commit_history, select
from .verification import verify_all


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = RadarConfig.from_path(args.config)
    issue_date = date.fromisoformat(args.issue_date) if args.issue_date else datetime.now(ZoneInfo(str(config.get("timezone", "Asia/Shanghai")))).date()
    history = load_json(args.history, {"version": 1, "recommended": {}})
    mailto = os.getenv("QQ_EMAIL", "").strip()
    if args.candidates:
        payload = load_json(args.candidates)
        candidates = payload.get("papers", payload) if isinstance(payload, dict) else payload
        if not isinstance(candidates, list): raise RadarError("Candidate file must contain a list or {papers: [...]}")
        warnings: list[str] = []
    else:
        candidates, warnings = discover(config, issue_date, mailto)
    source_counts: dict[str, int] = {}
    for candidate in candidates:
        for source in candidate.get("source_labels", []):
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1
    candidate_top = [
        bool(candidate.get("top_journal_candidate"))
        or bool(assess_journal(candidate.get("journal"), config.journals, candidate.get("title"), candidate.get("abstract"), candidate.get("document_type_hint"))["top_journal"])
        for candidate in candidates
    ]
    candidate_stats = {
        "total": len(candidates),
        "formal": sum(not bool(x.get("is_preprint")) for x in candidates),
        "preprint": sum(bool(x.get("is_preprint")) for x in candidates),
        "top_journal": sum(candidate_top),
        "targeted_journal_hits": sum(bool(x.get("targeted_journal_hit")) for x in candidates),
        "sources": source_counts,
    }
    verified = verify_all(candidates, timeout=int(config.discovery.get("request_timeout_seconds", 30)))
    ds = config.deepseek
    ledger = BudgetLedger(args.usage, float(config.get("monthly_budget_cny")), float(ds.get("input_cny_per_million", 1)), float(ds.get("output_cny_per_million", 2)), issue_date)
    client = DeepSeekClient(config, ledger)
    screened, rejected = screen_all(verified, client, config.journals)
    selection = select(screened, history, issue_date, config)
    summarized = summarize_selected(selection, client)
    selection["excluded"] = rejected + selection["excluded"]
    selection["rejection_summary"] = dict(Counter(item.get("reason", "unknown") for item in selection["excluded"]).most_common(10))
    selection["source_warnings"] = warnings
    selection["candidate_stats"] = candidate_stats
    selection["model_stats"] = {"screened": len(verified), "eligible_after_screening": len(screened), "summarized": summarized}
    selection["budget"] = {"month": ledger.month_key, "estimated_spent_cny": ledger.spent(), "limit_cny": ledger.monthly_limit}
    html_body = render(selection, warnings)
    mail_subject = subject(args.mode, issue_date.isoformat(), len(selection["selected_formal"]))
    args.output.mkdir(parents=True, exist_ok=True)
    html_path = args.output / f"digest-{issue_date.isoformat()}.html"
    selection_path = args.output / f"selection-{issue_date.isoformat()}.json"
    html_path.write_text(html_body, encoding="utf-8")
    write_json_atomic(selection_path, selection)
    (args.output / f"subject-{issue_date.isoformat()}.txt").write_text(mail_subject + "\n", encoding="utf-8")
    sent = False
    if not args.dry_run:
        send_html(mail_subject, html_body)
        sent = True
    history_committed = False
    if sent and args.mode == "production":
        write_json_atomic(args.history, commit_history(history, selection, issue_date))
        history_committed = True
    return {"issue_date": issue_date.isoformat(), "candidates": len(candidates), "candidate_stats": candidate_stats, "screened": len(screened), "summarized": summarized, "formal": len(selection["selected_formal"]), "reviews": len(selection["selected_reviews"]), "preprints": len(selection["preprint_watchlist"]), "sent": sent, "history_committed": history_committed, "output": str(html_path), "budget_cny": ledger.spent(), "warnings": len(warnings)}


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    result = argparse.ArgumentParser(description="Run the ME × Protein weekly radar")
    result.add_argument("--config", type=Path, default=root / "config" / "radar.json")
    result.add_argument("--history", type=Path, default=root / "data" / "history.json")
    result.add_argument("--usage", type=Path, default=root / "data" / "usage.json")
    result.add_argument("--output", type=Path, default=root / "output")
    result.add_argument("--issue-date", default="")
    result.add_argument("--mode", choices=("test", "production"), default="test")
    result.add_argument("--dry-run", action="store_true", help="Generate artifacts but do not send email or update history")
    result.add_argument("--candidates", type=Path, help="Optional raw candidate JSON for controlled runs")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    issue = args.issue_date or date.today().isoformat()
    try:
        print(json.dumps(run(args), ensure_ascii=False))
        return 0
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {error}", file=sys.stderr)
        if not args.dry_run:
            try: send_alert(issue, error)
            except Exception as alert_exc: print(f"ALERT ERROR: {alert_exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
