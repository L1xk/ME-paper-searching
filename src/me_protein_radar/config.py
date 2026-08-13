from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import RadarError, load_json


@dataclass(frozen=True)
class RadarConfig:
    raw: dict[str, Any]

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
        if not (0 <= int(raw.get("max_preprint_semantic_candidates", 0)) <= int(raw.get("max_semantic_candidates", 80))):
            raise RadarError("Invalid preprint candidate quota")
        if float(raw["monthly_budget_cny"]) <= 0:
            raise RadarError("monthly_budget_cny must be positive")
        return cls(raw)

    def get(self, name: str, default: Any = None) -> Any:
        return self.raw.get(name, default)

    @property
    def deepseek(self) -> dict[str, Any]:
        return self.raw["deepseek"]

    @property
    def discovery(self) -> dict[str, Any]:
        return self.raw["discovery"]
