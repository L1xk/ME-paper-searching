from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .config import RadarConfig
from .http import request_json
from .io_utils import RadarError, clean_text, is_top_journal, load_json, write_json_atomic


Transport = Callable[..., Any]
ALLOWED_TRACKS = {"integrated", "metabolic_engineering", "enzyme_engineering", "ai_protein", "irrelevant"}
ALLOWED_TYPES = {"article", "review", "perspective", "comment", "editorial", "bibliometric", "preprint"}


@dataclass
class BudgetLedger:
    path: Path
    monthly_limit: float
    input_rate: float
    output_rate: float
    today: date

    def _state(self) -> dict[str, Any]:
        state = load_json(self.path, {"version": 1, "months": {}})
        if not isinstance(state, dict):
            raise RadarError("Invalid usage ledger")
        state.setdefault("version", 1)
        state.setdefault("months", {})
        return state

    @property
    def month_key(self) -> str:
        return self.today.strftime("%Y-%m")

    def spent(self) -> float:
        month = self._state()["months"].get(self.month_key, {})
        return float(month.get("estimated_cny", 0.0)) + float(month.get("unconfirmed_cny", 0.0))

    def estimate(self, prompt_chars: int, max_output_tokens: int) -> float:
        prompt_tokens = math.ceil(prompt_chars / 3)
        return prompt_tokens / 1_000_000 * self.input_rate + max_output_tokens / 1_000_000 * self.output_rate

    def ensure_available(self, estimated_next: float) -> None:
        if self.spent() + estimated_next > self.monthly_limit:
            raise RadarError(f"DeepSeek monthly budget guard stopped the run: spent/estimated {self.spent():.4f} + {estimated_next:.4f} CNY exceeds {self.monthly_limit:.2f} CNY")

    def reserve(self, estimated_cny: float, model: str) -> float:
        self.ensure_available(estimated_cny)
        state = self._state()
        month = state["months"].setdefault(self.month_key, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "attempts": 0, "estimated_cny": 0.0, "unconfirmed_cny": 0.0, "model": model})
        month["attempts"] = int(month.get("attempts", 0)) + 1
        month["unconfirmed_cny"] = round(float(month.get("unconfirmed_cny", 0.0)) + estimated_cny, 6)
        write_json_atomic(self.path, state)
        return estimated_cny

    def settle(self, reserved_cny: float, prompt_tokens: int, completion_tokens: int, model: str) -> None:
        state = self._state()
        month = state["months"].setdefault(self.month_key, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "attempts": 0, "estimated_cny": 0.0, "unconfirmed_cny": 0.0, "model": model})
        month["unconfirmed_cny"] = round(max(0.0, float(month.get("unconfirmed_cny", 0.0)) - reserved_cny), 6)
        month["prompt_tokens"] = int(month.get("prompt_tokens", 0)) + int(prompt_tokens)
        month["completion_tokens"] = int(month.get("completion_tokens", 0)) + int(completion_tokens)
        month["calls"] = int(month.get("calls", 0)) + 1
        month["model"] = model
        month["estimated_cny"] = round(month["prompt_tokens"] / 1_000_000 * self.input_rate + month["completion_tokens"] / 1_000_000 * self.output_rate, 6)
        write_json_atomic(self.path, state)


def system_prompt() -> str:
    return """你是微生物代谢工程、酶工程和 AI for Protein 文献筛选员。只能依据输入的题录、公开摘要或开放全文片段，不得补写证据中没有的数据。
判定规则：
1. 排除植物、动物细胞等非菌株代谢工程；微生物群落可纳入；无细胞系统仅在直接服务酶工程或通路验证时纳入。
2. 代谢工程需有真实微生物宿主、产物/通路工程问题；不能因出现 metabolic engineering 等词就判相关。
3. 酶工程需包含酶/生物催化对象及设计、改造、定向进化、筛选、固定化或性能评价。
4. AI for Protein 需同时有蛋白对象、AI 方法、明确蛋白任务；原创研究必须有湿实验验证。通用结构预测/蛋白设计只有满足这些条件才纳入。
5. 综述可纳入科研视角，不要求湿实验，但必须高度相关。原创 AI for Protein 的优先级低于代谢工程及二者结合。
6. 摘要级证据只能总结摘要明确陈述的内容。
输出严格 JSON 对象，不要 Markdown。中文字段应精炼、具体、避免宣传口吻。"""


def user_prompt(record: dict[str, Any]) -> str:
    evidence = clean_text(record.get("evidence_text"))[:14000]
    payload = {
        "title": record.get("title"),
        "journal": record.get("journal"),
        "date": record.get("publication_date"),
        "document_type_hint": record.get("document_type_hint"),
        "is_preprint": record.get("is_preprint"),
        "verification_level": record.get("verification_level"),
        "evidence": evidence,
    }
    schema = {
        "eligible": "boolean",
        "document_type": "article|review|perspective|comment|editorial|bibliometric|preprint",
        "track": "integrated|metabolic_engineering|enzyme_engineering|ai_protein|irrelevant",
        "domain": "microbial|microbial_community|cell_free|plant|animal|other",
        "cell_free_direct_support": "boolean",
        "wet_lab": "boolean; original AI for Protein must be explicitly supported",
        "host_or_object": "string",
        "product_or_task": "string",
        "methods": ["up to 5 strings"],
        "base_score": "integer 0-100, relevance/novelty/engineering depth/transferability/evidence quality",
        "title_zh": "Chinese title",
        "recommendation_reason_zh": "1-2 Chinese sentences explaining why it is worth reading",
        "summary_zh": "100-180 Chinese characters: question, method, major result only if supported, significance and evidence boundary",
        "evidence_scope": "what was actually verified",
        "uncertainty_note": "string, empty if none"
    }
    return "请按 schema 判定这篇论文。INPUT=" + json.dumps(payload, ensure_ascii=False) + "\nSCHEMA=" + json.dumps(schema, ensure_ascii=False)


def validate_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RadarError("DeepSeek result is not a JSON object")
    required = {"eligible", "document_type", "track", "domain", "cell_free_direct_support", "wet_lab", "host_or_object", "product_or_task", "methods", "base_score", "title_zh", "recommendation_reason_zh", "summary_zh", "evidence_scope", "uncertainty_note"}
    missing = required - value.keys()
    if missing:
        raise RadarError(f"DeepSeek JSON missing fields: {', '.join(sorted(missing))}")
    if type(value["eligible"]) is not bool or type(value["wet_lab"]) is not bool or type(value["cell_free_direct_support"]) is not bool:
        raise RadarError("DeepSeek JSON has invalid boolean fields")
    if value["track"] not in ALLOWED_TRACKS or value["document_type"] not in ALLOWED_TYPES:
        raise RadarError("DeepSeek JSON has invalid track or document_type")
    if not isinstance(value["methods"], list) or not 0 <= float(value["base_score"]) <= 100:
        raise RadarError("DeepSeek JSON has invalid methods or base_score")
    if value["eligible"]:
        for field in ("title_zh", "recommendation_reason_zh", "summary_zh", "evidence_scope"):
            if not clean_text(value[field]):
                raise RadarError(f"DeepSeek JSON has empty {field}")
    return value


class DeepSeekClient:
    def __init__(self, config: RadarConfig, ledger: BudgetLedger, transport: Transport = request_json, api_key: str | None = None):
        self.settings = config.deepseek
        self.ledger = ledger
        self.transport = transport
        self.api_key = (api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")).strip()
        if not self.api_key:
            raise RadarError("Missing DEEPSEEK_API_KEY")

    def analyze(self, record: dict[str, Any]) -> dict[str, Any]:
        prompt = user_prompt(record)
        max_tokens = int(self.settings.get("max_output_tokens", 1200))
        attempt_estimate = self.ledger.estimate(len(prompt) + len(system_prompt()), max_tokens)
        body = json.dumps({"model": self.settings.get("model", "deepseek-v4-flash"), "messages": [{"role": "system", "content": system_prompt()}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}, "temperature": 0.1, "max_tokens": max_tokens}, ensure_ascii=False).encode("utf-8")
        url = str(self.settings.get("base_url", "https://api.deepseek.com")).rstrip("/") + "/chat/completions"
        retries = int(self.settings.get("max_retries", 2))
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            reserved = self.ledger.reserve(attempt_estimate, str(self.settings.get("model")))
            try:
                response = self.transport(url, data=body, timeout=int(self.settings.get("timeout_seconds", 60)), headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, retries=0)
                usage = response.get("usage") or {}
                self.ledger.settle(reserved, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), str(self.settings.get("model")))
                content = response["choices"][0]["message"]["content"]
                if not clean_text(content):
                    raise RadarError("DeepSeek returned empty JSON content")
                return validate_result(json.loads(content))
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, RadarError) as exc:
                last_error = exc
                if attempt == retries:
                    break
        raise RadarError(f"DeepSeek structured analysis failed after {retries + 1} attempts: {last_error}")


def analyze_all(records: list[dict[str, Any]], client: DeepSeekClient, top_journals: list[str], top_aliases: list[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for record in records:
        if record.get("verification_level") == "metadata":
            rejected.append({"title": record.get("title", ""), "reason": "metadata-only"})
            continue
        analysis = client.analyze(record)
        if not analysis["eligible"] or analysis["track"] == "irrelevant":
            rejected.append({"title": record.get("title", ""), "reason": clean_text(analysis.get("uncertainty_note")) or "semantic screening: irrelevant"})
            continue
        merged = dict(record)
        merged.update(analysis)
        merged["semantic_relevance_verified"] = True
        merged["summary_model"] = str(client.settings.get("model"))
        merged["summary_validated"] = True
        merged["top_journal"] = is_top_journal(merged.get("journal"), top_journals, top_aliases)
        merged["is_preprint"] = bool(record.get("is_preprint")) or analysis.get("document_type") == "preprint"
        if merged["is_preprint"]:
            merged["document_type"] = "preprint"
        accepted.append(merged)
    return accepted, rejected
