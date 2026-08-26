from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date, timedelta
from typing import Any

from .config import RadarConfig
from .io_utils import RadarError, clean_text, journal_key, normalize_doi, parse_date, rolling_years_before


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
    item["is_preprint"] = bool(item.get("is_preprint")) or item.get("document_type") == "preprint"
    if item["is_preprint"]:
        return None, "preprints disabled"
    published = parse_date(item.get("publication_date"))
    if not published or not rolling_years_before(issue_date, int(config.get("rolling_years", 6))) <= published <= issue_date:
        return None, "outside rolling six-year window"
    if item.get("domain") in {"plant", "animal", "other"}: return None, "excluded biological domain"
    if item.get("domain") == "cell_free" and not item.get("cell_free_direct_support"): return None, "cell-free lacks direct support"
    if item.get("verification_level") == "metadata": return None, "metadata-only"
    if item.get("track") == "ai_protein" and item.get("document_type") not in {"review"} and not item.get("wet_lab"):
        return None, "AI for Protein lacks wet-lab validation"
    is_top = bool(item.get("top_journal"))
    exceptional_score = float(config.get("exceptional_non_top_min_base_score", 94))
    exceptional = (
        not is_top
        and item.get("document_type") == "article"
        and bool(item.get("exceptional_novelty"))
        and item.get("novelty_category") not in {None, "", "none"}
        and bool(clean_text(item.get("novelty_evidence_zh")))
        and float(item.get("base_score", 0)) >= exceptional_score
    )
    if not is_top and not exceptional:
        return None, "non-Top journal without verified exceptional novelty"
    item["quality_tier"] = "top_journal" if is_top else "exceptional_non_top"
    top_bonus = float(config.get("top_journal_bonus", 8)) if item.get("top_journal") and not item["is_preprint"] else 0
    score = float(item.get("base_score", 0)) + TRACK_ADJUSTMENT.get(str(item.get("track")), -30) + TYPE_ADJUSTMENT.get(str(item.get("document_type")), 0) + top_bonus
    item["final_score"] = round(min(100, max(0, score)), 2)
    if item["final_score"] < float(config.get("score_threshold", 72)): return None, "below score threshold"
    if not (item.get("doi") or item.get("url")): return None, "missing stable link"
    item["age_pool"] = "recent" if issue_date - published <= timedelta(days=int(config.get("recent_days", 30))) else "historical"
    item["record_key"] = paper_key(item)
    return item, "eligible"


def _rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda x: (bool(x.get("top_journal")), float(x["final_score"]), x.get("publication_date", ""), int(x.get("citation_count") or 0)), reverse=True)


def _venue_key(item: dict[str, Any]) -> str:
    return journal_key(item.get("canonical_journal") or item.get("journal")) or "unknown"


def _take_diverse(
    pool: list[dict[str, Any]],
    target: int,
    selected: list[dict[str, Any]],
    config: RadarConfig,
) -> tuple[list[dict[str, Any]], list[str]]:
    if target <= 0:
        return [], []
    chosen: list[dict[str, Any]] = []
    relaxations: list[str] = []
    selected_keys = {str(item.get("record_key")) for item in selected}
    max_journal = int(config.get("max_same_journal", 2))
    max_track = int(config.get("max_same_track", 4))
    stages = [(True, True, "strict")]
    if config.get("relax_diversity_to_meet_quotas", True):
        stages += [(True, False, "track"), (False, False, "journal")]
    for enforce_journal, enforce_track, label in stages:
        for item in pool:
            key = str(item.get("record_key"))
            if key in selected_keys or item in chosen:
                continue
            current = selected + chosen
            venue_counts = Counter(_venue_key(row) for row in current)
            track_counts = Counter(str(row.get("track")) for row in current)
            if enforce_journal and venue_counts[_venue_key(item)] >= max_journal:
                continue
            if enforce_track and track_counts[str(item.get("track"))] >= max_track:
                continue
            chosen.append(item)
            if label != "strict":
                relaxations.append(f"{label}:{clean_text(item.get('title'))}")
            if len(chosen) >= target:
                return chosen, relaxations
    return chosen, relaxations


def select(records: list[dict[str, Any]], history: dict[str, Any], issue_date: date, config: RadarConfig) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for record in records:
        if seen(record, history):
            excluded.append({"title": record.get("title", ""), "reason": "already recommended"}); continue
        item, reason = prepare(record, issue_date, config)
        if item is None: excluded.append({"title": record.get("title", ""), "reason": reason})
        else: eligible.append(item)
    reviews = _rank([x for x in eligible if not x.get("is_preprint") and x.get("document_type") == "review"])
    research = _rank([x for x in eligible if not x.get("is_preprint") and x.get("document_type") == "article"])
    review_min, review_max = int(config.get("review_min", 2)), int(config.get("review_max", 3))
    if len(reviews) < review_min: raise RadarError(f"Review quota unmet: found {len(reviews)}, need {review_min}")
    chosen_reviews, diversity_relaxations = _take_diverse(reviews, review_min, [], config)
    if len(chosen_reviews) < review_min: raise RadarError(f"Review quota unmet after diversity selection: found {len(chosen_reviews)}, need {review_min}")
    min_total, max_total = int(config.get("formal_min", 8)), int(config.get("formal_max", 15))
    hist_min, hist_max = int(config.get("historical_min", 0)), int(config.get("historical_max", 8))
    chosen: list[dict[str, Any]] = []
    formal_pool = _rank(research)
    historical = [x for x in formal_pool if x["age_pool"] == "historical"]
    recent = [x for x in formal_pool if x["age_pool"] == "recent"]
    review_hist = sum(x["age_pool"] == "historical" for x in chosen_reviews)
    incoming, relaxed = _take_diverse(historical, max(0, hist_max - review_hist), chosen_reviews, config)
    chosen.extend(incoming)
    diversity_relaxations.extend(relaxed)
    needed = max(0, min_total - len(chosen_reviews) - len(chosen))
    incoming, relaxed = _take_diverse(recent, needed, chosen_reviews + chosen, config)
    chosen.extend(incoming)
    diversity_relaxations.extend(relaxed)
    if len(chosen) + len(chosen_reviews) < min_total:
        incoming, relaxed = _take_diverse(recent, min_total - len(chosen) - len(chosen_reviews), chosen_reviews + chosen, config)
        chosen.extend(incoming)
        diversity_relaxations.extend(relaxed)
    if len(chosen) + len(chosen_reviews) < min_total:
        current_hist = sum(x["age_pool"] == "historical" for x in chosen + chosen_reviews)
        extra_review_pool = [item for item in reviews if item not in chosen_reviews and (item["age_pool"] != "historical" or current_hist < hist_max)]
        extra_target = min(review_max - len(chosen_reviews), min_total - len(chosen) - len(chosen_reviews))
        incoming, relaxed = _take_diverse(extra_review_pool, extra_target, chosen_reviews + chosen, config)
        chosen_reviews.extend(incoming)
        diversity_relaxations.extend(relaxed)
    current_hist = sum(x["age_pool"] == "historical" for x in chosen + chosen_reviews)
    if current_hist < hist_min and len(chosen_reviews) < review_max:
        historic_reviews = [x for x in reviews if x not in chosen_reviews and x["age_pool"] == "historical"]
        replaceable = [x for x in chosen if x["age_pool"] == "recent"]
        for candidate, outgoing in zip(historic_reviews, replaceable):
            if current_hist >= hist_min or len(chosen_reviews) >= review_max: break
            incoming_rows, relaxed = _take_diverse([candidate], 1, [x for x in chosen + chosen_reviews if x is not outgoing], config)
            if not incoming_rows:
                continue
            chosen.remove(outgoing)
            chosen_reviews.append(incoming_rows[0])
            diversity_relaxations.extend(relaxed)
            current_hist += 1
    formal = _rank(chosen + chosen_reviews)[:max_total]
    if any(x.get("is_preprint") or x.get("document_type") == "preprint" for x in formal):
        raise RadarError("Formal selection contains a preprint")
    hist_count = sum(x["age_pool"] == "historical" for x in formal)
    if len(formal) < min_total: raise RadarError(f"Formal quota unmet: found {len(formal)}, need {min_total}")
    if hist_count < hist_min: raise RadarError(f"Historical quota unmet: found {hist_count}, need {hist_min}")
    if hist_count > hist_max: raise RadarError(f"Historical quota exceeded: found {hist_count}, maximum {hist_max}")
    top_count = sum(bool(x.get("top_journal")) for x in formal)
    top_min = int(config.get("top_journal_min", 1))
    if top_count < top_min: raise RadarError(f"Top journal quota unmet: found {top_count}, need {top_min}")
    non_top_count = len(formal) - top_count
    non_top_max = int(config.get("exceptional_non_top_max", 0))
    if non_top_count > non_top_max: raise RadarError(f"Exceptional non-Top quota exceeded: found {non_top_count}, maximum {non_top_max}")
    venue_counts = Counter(_venue_key(item) for item in formal)
    track_counts = Counter(str(item.get("track")) for item in formal)
    return {
        "issue_date": issue_date.isoformat(),
        "selected_formal": formal,
        "selected_research": [x for x in formal if x.get("document_type") == "article"],
        "selected_reviews": [x for x in formal if x.get("document_type") == "review"],
        "preprint_watchlist": [],
        "excluded": excluded,
        "diversity_summary": {
            "journal_counts": dict(venue_counts),
            "track_counts": dict(track_counts),
            "relaxations": diversity_relaxations,
        },
    }


def commit_history(history: dict[str, Any], selection: dict[str, Any], issue_date: date) -> dict[str, Any]:
    result = {"version": 1, "recommended": dict(history.get("recommended", {}))}
    for item in selection["selected_formal"]:
        result["recommended"][item["record_key"]] = {"issue_date": issue_date.isoformat(), "title": item.get("title", ""), "first_author": item.get("first_author", ""), "doi": normalize_doi(item.get("doi"))}
    return result
