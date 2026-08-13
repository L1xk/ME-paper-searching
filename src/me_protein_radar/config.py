from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import RadarError, load_json


@dataclass(frozen=True)
class RadarConfig:
    raw: dict[str, Any]
    path: Path
    journals: list[dict[str, Any]]

    @classmethod
    def from_path(cls, path: Path) -> "RadarConfig":
        raw = load_json(path)
        if not isinstance(raw, dict):
            raise RadarError("Configuration must be a JSON object")
        required = ("rolling_years", "formal_min", "formal_max", "monthly_budget_cny", "deepseek", "discovery")
        missing = [name for name in required if name not in raw]
        if missing:
            raise RadarError(f"Configuration is missing: {', '.join(missing)}")
        if not (0 <= int(raw["formal_min"]) <= int(raw["formal_max"])):
            raise RadarError("Invalid formal paper quota")
        if not (0 <= int(raw.get("top_journal_min", 0)) <= int(raw["formal_max"])):
            raise RadarError("Invalid Top journal quota")
        if not (0 <= int(raw.get("exceptional_non_top_max", 0)) <= int(raw["formal_max"])):
            raise RadarError("Invalid exceptional non-Top quota")
        if not (0 <= float(raw.get("exceptional_non_top_min_base_score", 94)) <= 100):
            raise RadarError("Invalid exceptional non-Top score threshold")
        if not (0 <= int(raw.get("max_preprint_semantic_candidates", 0)) <= int(raw.get("max_semantic_candidates", 80))):
            raise RadarError("Invalid preprint candidate quota")
        candidate_limit = int(raw.get("max_semantic_candidates", 100))
        for name in ("top_candidate_reserve", "review_candidate_reserve", "recent_candidate_reserve"):
            if not (0 <= int(raw.get(name, 0)) <= candidate_limit):
                raise RadarError(f"Invalid candidate reserve: {name}")
        if float(raw["monthly_budget_cny"]) <= 0:
            raise RadarError("monthly_budget_cny must be positive")
        catalog_name = str(raw.get("journal_catalog", "journals.json"))
        catalog_path = path.parent / catalog_name
        catalog = load_json(catalog_path)
        journals = catalog.get("journals") if isinstance(catalog, dict) else None
        if not isinstance(journals, list) or not journals:
            raise RadarError(f"Journal catalog must contain a non-empty journals list: {catalog_path}")
        seen_names: set[str] = set()
        for journal in journals:
            if not isinstance(journal, dict) or not str(journal.get("name", "")).strip():
                raise RadarError("Journal catalog contains an invalid entry")
            name_key = str(journal["name"]).casefold()
            if name_key in seen_names:
                raise RadarError(f"Journal catalog contains duplicate canonical name: {journal['name']}")
            seen_names.add(name_key)
            if journal.get("policy") not in {"top", "conditional_top", "review_top"}:
                raise RadarError(f"Journal catalog has invalid policy: {journal['name']}")
            if journal.get("policy") == "conditional_top" and not journal.get("scope_terms"):
                raise RadarError(f"Conditional journal lacks scope terms: {journal['name']}")
        for name in ("max_same_journal", "max_same_track"):
            if int(raw.get(name, 1)) < 1:
                raise RadarError(f"Invalid diversity limit: {name}")
        return cls(raw, path, journals)

    def get(self, name: str, default: Any = None) -> Any:
        return self.raw.get(name, default)

    @property
    def deepseek(self) -> dict[str, Any]:
        return self.raw["deepseek"]

    @property
    def discovery(self) -> dict[str, Any]:
        return self.raw["discovery"]

    @property
    def top_journal_names(self) -> list[str]:
        return [str(item["name"]) for item in self.journals]

    @property
    def top_journal_aliases(self) -> list[str]:
        return [str(alias) for item in self.journals for alias in item.get("aliases", [])]
