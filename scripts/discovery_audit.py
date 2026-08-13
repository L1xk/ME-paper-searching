from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

from me_protein_radar.config import RadarConfig
from me_protein_radar.discovery import discover


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Read-only discovery audit; never calls DeepSeek or sends email")
    parser.add_argument("--config", type=Path, default=root / "config" / "radar.json")
    parser.add_argument("--issue-date", default=date.today().isoformat())
    args = parser.parse_args()
    config = RadarConfig.from_path(args.config)
    candidates, warnings = discover(config, date.fromisoformat(args.issue_date), "")
    source_counts = Counter(source for item in candidates for source in item.get("source_labels", []))
    journal_counts = Counter(str(item.get("canonical_journal") or item.get("journal") or "unknown") for item in candidates)
    top_journal_counts = Counter(
        str(item.get("canonical_journal") or item.get("journal") or "unknown")
        for item in candidates if item.get("top_journal_candidate")
    )
    result = {
        "issue_date": args.issue_date,
        "total": len(candidates),
        "formal": sum(not bool(item.get("is_preprint")) for item in candidates),
        "preprint": sum(bool(item.get("is_preprint")) for item in candidates),
        "top_journal_candidates": sum(bool(item.get("top_journal_candidate")) for item in candidates),
        "targeted_journal_hits": sum(bool(item.get("targeted_journal_hit")) for item in candidates),
        "review_candidates": sum("reviews" in item.get("query_families", []) or "review" in str(item.get("document_type_hint", "")) for item in candidates),
        "sources": dict(source_counts),
        "journal_distribution": dict(journal_counts.most_common(20)),
        "top_journal_distribution": dict(top_journal_counts.most_common(20)),
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
