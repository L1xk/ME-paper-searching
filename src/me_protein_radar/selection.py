from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Any

from .config import RadarConfig
from .io_utils import RadarError, normalize_doi, parse_date, rolling_years_before


TRACK_ADJUSTMENT = {"integrated": 8, "metabolic_engineering": 5, "enzyme_engineering": 2, "ai_protein": -5}
TYPE_ADJUSTMENT = {"review": -4, "perspective": -15, "comment": -18, "editorial": -18, "bibliometric": -20, "preprint": -2}


def paper_key(record: dict[str, Any]) -> str:
    doi = normalize_doi(record.get("doi"))
    if doi: return "doi:" + doi
    title = re.sub(r"[^a-z0-9]+", " ", str(record.get("title", "")).lower()).strip()
    author = str(record.get("first_author", "")).lower()
    return "title:" + hashlib.sha256(f"{author}|{title}".encode()).hexdigest()[:24]


def title_similarity(a: str, b: str) -> float:
    left = set(re.findall(r"[a-z0-9]+", a.lower()))
    right = set(re.findall(r"[a-z0-9]+", b.lower()))
    return len(left & right) / len(left | right) if left and right else 0


def seen(record: dict[str, Any], history: dict[str, Any]) -> bool:
    recommended = history.get("recommended", {})
    if paper_key(record) in recommended: return True
    doi = normalize_doi(record.get("doi"))
    for past in recommended.values():
        if doi and doi == normalize_doi(past.get("doi")): return True
        if record.get("first_author") and str(record.get("first_author")).lower() == str(past.get("first_author", "")).lower() and title_similarity(str(record.get("title", "")), str(past.get("title", ""))) >= .9:
            return True
    return False


def prepare(record: dict[str, Any], issue_date: date, config: RadarConfig) -> tuple[dict[str, Any] | None, str]:
    item = dict(record)
    published = parse_date(item.get("publication_date"))
    if not published or not rolling_years_before(issue_date, int(config.get("rolling_years", 6))) <= published <= issue_date:
        return None, "outside rolling six-year window"
    if item.get("domain") in {"plant", "animal", "other"}: return None, "excluded biological domain"
    if item.get("domain") == "cell_free" and not item.get("cell_free_direct_support"): return None, "cell-free lacks direct support"
    if item.get("verification_level") == "metadata": return None, "metadata-only"
    if item.get("track") == "ai_protein" and item.get("document_type") not in {"review"} and not item.get("wet_lab"):
        return None, "AI for Protein lacks wet-lab validation"
    score = float(item.get("base_score", 0)) + TRACK_ADJUSTMENT.get(str(item.get("track")), -30) + TYPE_ADJUSTMENT.get(str(item.get("document_type")), 0)
    item["final_score"] = round(min(100, max(0, score)), 2)
    if item["final_score"] < float(config.get("score_threshold", 72)): return None, "below score threshold"
    if not (item.get("doi") or item.get("url")): return None, "missing stable link"
    item["age_pool"] = "recent" if issue_date - published <= timedelta(days=int(config.get("recent_days", 30))) else "historical"
    item["record_key"] = paper_key(item)
    return item, "eligible"


def _rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda x: (float(x["final_score"]), x.get("publication_date", ""), int(x.get("citation_count") or 0)), reverse=True)


def select(records: list[dict[str, Any]], history: dict[str, Any], issue_date: date, config: RadarConfig) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for record in records:
        if seen(record, history):
            excluded.append({"title": record.get("title", ""), "reason": "already recommended"}); continue
        item, reason = prepare(record, issue_date, config)
        if item is None: excluded.append({"title": record.get("title", ""), "reason": reason})
        else: eligible.append(item)
    preprints = _rank([x for x in eligible if x.get("is_preprint")])[:int(config.get("preprint_max", 5))]
    reviews = _rank([x for x in eligible if not x.get("is_preprint") and x.get("document_type") == "review"])
    research = _rank([x for x in eligible if not x.get("is_preprint") and x.get("document_type") == "article"])
    review_min, review_max = int(config.get("review_min", 2)), int(config.get("review_max", 3))
    if len(reviews) < review_min: raise RadarError(f"Review quota unmet: found {len(reviews)}, need {review_min}")
    chosen_reviews = reviews[:review_min]
    min_total, max_total = int(config.get("formal_min", 10)), int(config.get("formal_max", 15))
    hist_min, hist_max = int(config.get("historical_min", 7)), int(config.get("historical_max", 8))
    chosen: list[dict[str, Any]] = []
    formal_pool = _rank(research)
    historical = [x for x in formal_pool if x["age_pool"] == "historical"]
    recent = [x for x in formal_pool if x["age_pool"] == "recent"]
    review_hist = sum(x["age_pool"] == "historical" for x in chosen_reviews)
    chosen.extend(historical[:max(0, hist_max - review_hist)])
    needed = max(0, min_total - len(chosen_reviews) - len(chosen))
    chosen.extend(recent[:needed])
    if len(chosen) + len(chosen_reviews) < min_total:
        for item in recent:
            if item not in chosen and len(chosen) + len(chosen_reviews) < min_total: chosen.append(item)
    if len(chosen) + len(chosen_reviews) < min_total:
        for item in reviews[review_min:review_max]:
            current_hist = sum(x["age_pool"] == "historical" for x in chosen + chosen_reviews)
            if len(chosen) + len(chosen_reviews) < min_total and (item["age_pool"] != "historical" or current_hist < hist_max):
                chosen_reviews.append(item)
    current_hist = sum(x["age_pool"] == "historical" for x in chosen + chosen_reviews)
    if current_hist < hist_min and len(chosen_reviews) < review_max:
        historic_reviews = [x for x in reviews if x not in chosen_reviews and x["age_pool"] == "historical"]
        replaceable = [x for x in chosen if x["age_pool"] == "recent"]
        for incoming, outgoing in zip(historic_reviews, replaceable):
            if current_hist >= hist_min or len(chosen_reviews) >= review_max: break
            chosen.remove(outgoing)
            chosen_reviews.append(incoming)
            current_hist += 1
    formal = _rank(chosen + chosen_reviews)[:max_total]
    hist_count = sum(x["age_pool"] == "historical" for x in formal)
    if len(formal) < min_total: raise RadarError(f"Formal quota unmet: found {len(formal)}, need {min_total}")
    if hist_count < hist_min: raise RadarError(f"Historical quota unmet: found {hist_count}, need {hist_min}")
    if hist_count > hist_max: raise RadarError(f"Historical quota exceeded: found {hist_count}, maximum {hist_max}")
    return {"issue_date": issue_date.isoformat(), "selected_formal": formal, "selected_research": [x for x in formal if x.get("document_type") == "article"], "selected_reviews": [x for x in formal if x.get("document_type") == "review"], "preprint_watchlist": preprints, "excluded": excluded}


def commit_history(history: dict[str, Any], selection: dict[str, Any], issue_date: date) -> dict[str, Any]:
    result = {"version": 1, "recommended": dict(history.get("recommended", {}))}
    for item in selection["selected_formal"] + selection["preprint_watchlist"]:
        result["recommended"][item["record_key"]] = {"issue_date": issue_date.isoformat(), "title": item.get("title", ""), "first_author": item.get("first_author", ""), "doi": normalize_doi(item.get("doi"))}
    return result
