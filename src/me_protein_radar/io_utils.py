from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any


class RadarError(RuntimeError):
    """Expected, user-actionable pipeline failure."""


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists() and default is not None:
        return default
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RadarError(f"Cannot read JSON {path}: {exc}") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp.replace(path)


def normalize_doi(value: Any) -> str:
    if not value:
        return ""
    doi = str(value).strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.rstrip(" .;,)")


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        match = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
        if not match:
            return None
        year = int(match.group(1))
        month = int(match.group(2) or 1)
        day = int(match.group(3) or 1)
        try:
            return date(year, month, day)
        except ValueError:
            return None


def rolling_years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, month=2, day=28)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def journal_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).casefold())


def is_top_journal(journal: Any, top_journals: list[str], aliases: list[str] | None = None) -> bool:
    accepted = {journal_key(name) for name in top_journals}
    accepted.update(journal_key(name) for name in (aliases or []))
    return bool(journal_key(journal)) and journal_key(journal) in accepted


def match_journal(journal: Any, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = journal_key(journal)
    if not key:
        return None
    for item in catalog:
        names = [item.get("name"), *(item.get("aliases") or [])]
        if key in {journal_key(name) for name in names if name}:
            return item
    return None


def assess_journal(
    journal: Any,
    catalog: list[dict[str, Any]],
    title: Any = "",
    evidence: Any = "",
    document_type: Any = "",
) -> dict[str, Any]:
    matched = match_journal(journal, catalog)
    if not matched:
        return {
            "canonical_name": clean_text(journal),
            "journal_policy": "unlisted",
            "journal_group": "unlisted",
            "top_journal": False,
            "journal_scope_terms": [],
        }
    policy = str(matched.get("policy"))
    context = clean_text(f"{title} {evidence}").casefold()
    scope_terms = [str(term) for term in matched.get("scope_terms", []) if str(term).casefold() in context]
    is_review = clean_text(document_type).casefold() == "review" or "review" in clean_text(document_type).casefold()
    if policy == "top":
        top = True
    elif policy == "review_top":
        top = is_review
    else:
        top = bool(scope_terms)
    return {
        "canonical_name": str(matched["name"]),
        "journal_policy": policy,
        "journal_group": str(matched.get("group", "top")),
        "top_journal": top,
        "journal_scope_terms": scope_terms,
    }


def first_nonempty(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "", [])), "")
