from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import RadarConfig
from .deepseek import BudgetLedger, DeepSeekClient, screen_all, summarize_selected
from .delivery import delivery_period, history_delivery_records, load_delivery_state, mark_delivered, update_automation_status, was_delivered
from .discovery import discover, review_candidate_hint
from .io_utils import RadarError, assess_journal, load_json, write_json_atomic
from .mailer import send_alert, send_html
from .render import render, subject
from .selection import commit_history, select
from .verification import verify_all


def _configured_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name)
        raise


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = RadarConfig.from_path(args.config)
    local_timezone = _configured_timezone(str(config.get("timezone", "Asia/Shanghai")))
    now = datetime.now(local_timezone)
    issue_date = date.fromisoformat(args.issue_date) if args.issue_date else now.date()
    period = delivery_period(issue_date)
    delivery_path = getattr(args, "delivery_state", args.history.parent / "delivery_state.json")
    status_path = getattr(args, "automation_status", args.history.parent / "automation_status.json")
    delivery_state = load_delivery_state(delivery_path)
    history = load_json(args.history, {"version": 1, "recommended": {}})
    recovered_records = history_delivery_records(history, period)
    state_delivered = was_delivered(delivery_state, period)
    if args.mode == "production" and not args.dry_run and (state_delivered or recovered_records):
        lock_source = "delivery_state" if state_delivered else "recommendation_history"
        if not state_delivered:
            recovered_issue_date = max(str(item.get("issue_date", issue_date.isoformat())) for item in recovered_records)
            write_json_atomic(
                delivery_path,
                mark_delivered(
                    delivery_state,
                    period,
                    issue_date=recovered_issue_date,
                    sent_at=recovered_issue_date + "T00:00:00+08:00",
                    formal_count=len(recovered_records),
                    review_count=0,
                    subject="Recovered from recommendation history",
                    source="history_recovery",
                ),
            )
        result = {
            "issue_date": issue_date.isoformat(),
            "period": period,
            "skipped": True,
            "skipped_reason": "already delivered for this ISO week",
            "sent": False,
            "history_committed": False,
            "budget_cny": 0.0,
        }
        update_automation_status(
            status_path,
            checked_at=now.isoformat(),
            issue_date=issue_date.isoformat(),
            period=period,
            mode=args.mode,
            outcome="already_delivered",
            trigger=os.getenv("GITHUB_EVENT_NAME", ""),
            run_id=os.getenv("GITHUB_RUN_ID", ""),
            details={"lock_source": lock_source},
        )
        return result
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
        "review_candidates": sum(review_candidate_hint(x) for x in candidates),
        "sources": source_counts,
    }
    verified = verify_all(candidates, timeout=int(config.discovery.get("request_timeout_seconds", 30)))
    ds = config.deepseek
    ledger = BudgetLedger(args.usage, float(config.get("monthly_budget_cny")), float(ds.get("input_cny_per_million", 1)), float(ds.get("output_cny_per_million", 2)), issue_date)
    client = DeepSeekClient(config, ledger)
    screened, rejected = screen_all(
        verified,
        client,
        config.journals,
        int(ds.get("max_consecutive_screen_failures", 5)),
    )
    try:
        selection = select(screened, history, issue_date, config)
    except RadarError as exc:
        screened_reviews = sum(item.get("document_type") == "review" for item in screened)
        screened_top = sum(bool(item.get("top_journal")) for item in screened)
        raise RadarError(
            f"{exc}; diagnostics: candidates={len(candidates)}, "
            f"review_candidates={candidate_stats['review_candidates']}, "
            f"screened_eligible={len(screened)}, screened_reviews={screened_reviews}, "
            f"screened_top={screened_top}"
        ) from exc
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
        write_json_atomic(
            delivery_path,
            mark_delivered(
                delivery_state,
                period,
                issue_date=issue_date.isoformat(),
                sent_at=datetime.now(local_timezone).isoformat(),
                formal_count=len(selection["selected_formal"]),
                review_count=len(selection["selected_reviews"]),
                subject=mail_subject,
            ),
        )
        history_committed = True
    if not args.dry_run:
        update_automation_status(
            status_path,
            checked_at=datetime.now(local_timezone).isoformat(),
            issue_date=issue_date.isoformat(),
            period=period,
            mode=args.mode,
            outcome="sent" if sent else "completed",
            trigger=os.getenv("GITHUB_EVENT_NAME", ""),
            run_id=os.getenv("GITHUB_RUN_ID", ""),
            details={
                "formal_count": len(selection["selected_formal"]),
                "review_count": len(selection["selected_reviews"]),
                "budget_cny": round(ledger.spent(), 6),
            },
        )
    return {"issue_date": issue_date.isoformat(), "period": period, "skipped": False, "candidates": len(candidates), "candidate_stats": candidate_stats, "screened": len(screened), "summarized": summarized, "formal": len(selection["selected_formal"]), "reviews": len(selection["selected_reviews"]), "preprints": len(selection["preprint_watchlist"]), "sent": sent, "history_committed": history_committed, "output": str(html_path), "budget_cny": ledger.spent(), "warnings": len(warnings)}


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    result = argparse.ArgumentParser(description="Run the ME × Protein weekly radar")
    result.add_argument("--config", type=Path, default=root / "config" / "radar.json")
    result.add_argument("--history", type=Path, default=root / "data" / "history.json")
    result.add_argument("--usage", type=Path, default=root / "data" / "usage.json")
    result.add_argument("--delivery-state", type=Path, default=root / "data" / "delivery_state.json")
    result.add_argument("--automation-status", type=Path, default=root / "data" / "automation_status.json")
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
            try:
                local_timezone = _configured_timezone("Asia/Shanghai")
                issue_date = date.fromisoformat(args.issue_date) if args.issue_date else datetime.now(local_timezone).date()
                update_automation_status(
                    args.automation_status,
                    checked_at=datetime.now(local_timezone).isoformat(),
                    issue_date=issue_date.isoformat(),
                    period=delivery_period(issue_date),
                    mode=args.mode,
                    outcome="failed",
                    trigger=os.getenv("GITHUB_EVENT_NAME", ""),
                    run_id=os.getenv("GITHUB_RUN_ID", ""),
                    details={"error_type": type(exc).__name__},
                )
            except Exception as status_exc:
                print(f"STATUS ERROR: {status_exc}", file=sys.stderr)
            try: send_alert(issue, error)
            except Exception as alert_exc: print(f"ALERT ERROR: {alert_exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
