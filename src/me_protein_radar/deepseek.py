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
from .io_utils import RadarError, assess_journal, clean_text, load_json, write_json_atomic


Transport = Callable[..., Any]
ALLOWED_TRACKS = {"integrated", "metabolic_engineering", "enzyme_engineering", "ai_protein", "irrelevant"}
ALLOWED_TYPES = {"article", "review", "perspective", "comment", "editorial", "bibliometric", "preprint"}
ALLOWED_NOVELTY = {"none", "new_complete_pathway", "new_product_route", "record_performance", "validated_ai_protein_method", "generalizable_enzyme_platform"}


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


def screen_system_prompt() -> str:
    return """你是微生物代谢工程、酶工程和 AI for Protein 文献筛选员。只能依据输入的题录、公开摘要或开放全文片段，不得补写证据中没有的数据。
判定规则：
1. 排除植物、动物细胞等非菌株代谢工程；微生物群落可纳入；无细胞系统仅在直接服务酶工程或通路验证时纳入。
2. 代谢工程需有真实微生物宿主、产物/通路工程问题；不能因出现 metabolic engineering 等词就判相关。
3. 酶工程需包含酶/生物催化对象及设计、改造、定向进化、筛选、固定化或性能评价。
4. AI for Protein 需同时有蛋白对象、AI 方法、明确蛋白任务；原创研究必须有湿实验验证。通用结构预测/蛋白设计只有满足这些条件才纳入。
5. 综述可纳入科研视角，不要求湿实验，但必须高度相关。原创 AI for Protein 的优先级低于代谢工程及二者结合。
5a. PubMed/Europe PMC 给出的 review 文献类型提示属于强元数据；只有摘要明确显示为原创研究时才可改判。review_search_hit 仅表示经综述专用检索召回，不能单独证明文献类型。
6. 摘要级证据只能总结摘要明确陈述的内容。
7. 普通期刊的原创研究只有在证据明确支持下列突破之一时才能标记 exceptional_novelty=true：首次完整新通路、首次新产物路线、带明确数值的领域领先性能、经湿实验验证且可迁移的 AI 蛋白方法、或可推广的酶工程平台。常规菌株优化、单酶活性提升、仅称“novel/first”但无实质证据、一般性模型应用均必须为 false。综述不得标记为创新例外。
输出严格 JSON 对象，不要 Markdown。此阶段只做准入、证据和评分，不生成题目翻译或长摘要。中文证据字段应精炼、具体、避免宣传口吻。"""


def screen_user_prompt(record: dict[str, Any]) -> str:
    evidence = clean_text(record.get("evidence_text"))[:14000]
    payload = {
        "title": record.get("title"),
        "journal": record.get("journal"),
        "date": record.get("publication_date"),
        "document_type_hint": record.get("document_type_hint"),
        "review_search_hit": bool(record.get("review_search_hit")),
        "query_families": record.get("query_families") or [],
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
        "exceptional_novelty": "boolean; use a very high bar, false for reviews and routine incremental work",
        "novelty_category": "none|new_complete_pathway|new_product_route|record_performance|validated_ai_protein_method|generalizable_enzyme_platform",
        "novelty_evidence_zh": "Chinese evidence sentence grounded in the supplied text; empty when exceptional_novelty is false",
        "evidence_scope": "what was actually verified",
        "uncertainty_note": "string, empty if none"
    }
    return "请按 schema 轻量筛选这篇论文。INPUT=" + json.dumps(payload, ensure_ascii=False) + "\nSCHEMA=" + json.dumps(schema, ensure_ascii=False)


def validate_screen_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RadarError("DeepSeek result is not a JSON object")
    required = {"eligible", "document_type", "track", "domain", "cell_free_direct_support", "wet_lab", "host_or_object", "product_or_task", "methods", "base_score", "exceptional_novelty", "novelty_category", "novelty_evidence_zh", "evidence_scope", "uncertainty_note"}
    missing = required - value.keys()
    if missing:
        raise RadarError(f"DeepSeek JSON missing fields: {', '.join(sorted(missing))}")
    if type(value["eligible"]) is not bool or type(value["wet_lab"]) is not bool or type(value["cell_free_direct_support"]) is not bool or type(value["exceptional_novelty"]) is not bool:
        raise RadarError("DeepSeek JSON has invalid boolean fields")
    if value["track"] not in ALLOWED_TRACKS or value["document_type"] not in ALLOWED_TYPES:
        raise RadarError("DeepSeek JSON has invalid track or document_type")
    if not isinstance(value["methods"], list) or not 0 <= float(value["base_score"]) <= 100:
        raise RadarError("DeepSeek JSON has invalid methods or base_score")
    if value["novelty_category"] not in ALLOWED_NOVELTY:
        raise RadarError("DeepSeek JSON has invalid novelty_category")
    if value["exceptional_novelty"]:
        if value["document_type"] != "article" or value["novelty_category"] == "none" or not clean_text(value["novelty_evidence_zh"]):
            raise RadarError("DeepSeek JSON has unsupported exceptional novelty")
    else:
        value["novelty_category"] = "none"
        value["novelty_evidence_zh"] = ""
    if value["eligible"] and not clean_text(value["evidence_scope"]):
        raise RadarError("DeepSeek JSON has empty evidence_scope")
    return value


def summary_system_prompt() -> str:
    return """你是微生物代谢工程、酶工程和 AI for Protein 文献编辑。论文已通过严格语义与期刊准入，只能依据输入证据生成中文阅读内容，不得补写证据中没有的数值、比较或结论。
输出严格 JSON，不要 Markdown。中文应准确、具体、克制；摘要包含研究问题、方法、证据明确支持的主要结果及意义，并主动保留证据边界。"""


def summary_user_prompt(record: dict[str, Any]) -> str:
    payload = {
        "title": record.get("title"),
        "journal": record.get("canonical_journal") or record.get("journal"),
        "document_type": record.get("document_type"),
        "track": record.get("track"),
        "host_or_object": record.get("host_or_object"),
        "product_or_task": record.get("product_or_task"),
        "methods": record.get("methods"),
        "verification_level": record.get("verification_level"),
        "screening_reason": record.get("novelty_evidence_zh"),
        "evidence": clean_text(record.get("evidence_text"))[:14000],
    }
    schema = {
        "title_zh": "accurate Chinese title",
        "recommendation_reason_zh": "1-2 Chinese sentences explaining the concrete reading value",
        "summary_zh": "100-180 Chinese characters covering question, method, supported result, significance and evidence boundary",
    }
    return "请为最终入选论文生成中文内容。INPUT=" + json.dumps(payload, ensure_ascii=False) + "\nSCHEMA=" + json.dumps(schema, ensure_ascii=False)


def validate_summary_result(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RadarError("DeepSeek summary is not a JSON object")
    required = {"title_zh", "recommendation_reason_zh", "summary_zh"}
    missing = required - value.keys()
    if missing:
        raise RadarError(f"DeepSeek summary missing fields: {', '.join(sorted(missing))}")
    result = {field: clean_text(value[field]) for field in required}
    if any(not result[field] for field in required):
        raise RadarError("DeepSeek summary contains an empty required field")
    return result


class DeepSeekClient:
    def __init__(self, config: RadarConfig, ledger: BudgetLedger, transport: Transport = request_json, api_key: str | None = None):
        self.settings = config.deepseek
        self.ledger = ledger
        self.transport = transport
        self.api_key = (api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")).strip()
        if not self.api_key:
            raise RadarError("Missing DEEPSEEK_API_KEY")

    def _structured_call(self, system: str, prompt: str, max_tokens: int, validator: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        attempt_estimate = self.ledger.estimate(len(prompt) + len(system), max_tokens)
        body = json.dumps({"model": self.settings.get("model", "deepseek-v4-flash"), "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}, "temperature": 0.1, "max_tokens": max_tokens}, ensure_ascii=False).encode("utf-8")
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
                return validator(json.loads(content))
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, RadarError) as exc:
                last_error = exc
                if attempt == retries:
                    break
        raise RadarError(f"DeepSeek structured analysis failed after {retries + 1} attempts: {last_error}")

    def screen(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._structured_call(
            screen_system_prompt(),
            screen_user_prompt(record),
            int(self.settings.get("screen_max_output_tokens", 650)),
            validate_screen_result,
        )

    def summarize(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._structured_call(
            summary_system_prompt(),
            summary_user_prompt(record),
            int(self.settings.get("summary_max_output_tokens", 900)),
            validate_summary_result,
        )


def screen_all(records: list[dict[str, Any]], client: DeepSeekClient, journal_catalog: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for record in records:
        if record.get("verification_level") == "metadata":
            rejected.append({"title": record.get("title", ""), "reason": "metadata-only"})
            continue
        analysis = client.screen(record)
        if not analysis["eligible"] or analysis["track"] == "irrelevant":
            rejected.append({"title": record.get("title", ""), "reason": clean_text(analysis.get("uncertainty_note")) or "semantic screening: irrelevant"})
            continue
        merged = dict(record)
        merged.update(analysis)
        merged["semantic_relevance_verified"] = True
        merged["screening_model"] = str(client.settings.get("model"))
        journal = assess_journal(merged.get("journal"), journal_catalog, merged.get("title"), merged.get("evidence_text"), merged.get("document_type"))
        merged.update({
            "canonical_journal": journal["canonical_name"],
            "journal_policy": journal["journal_policy"],
            "journal_group": journal["journal_group"],
            "journal_scope_terms": journal["journal_scope_terms"],
            "top_journal": journal["top_journal"],
        })
        merged["is_preprint"] = bool(record.get("is_preprint")) or analysis.get("document_type") == "preprint"
        if merged["is_preprint"]:
            merged["document_type"] = "preprint"
        accepted.append(merged)
    return accepted, rejected


def summarize_selected(selection: dict[str, Any], client: DeepSeekClient) -> int:
    count = 0
    for record in selection.get("selected_formal", []):
        record.update(client.summarize(record))
        record["summary_model"] = str(client.settings.get("model"))
        record["summary_validated"] = True
        count += 1
    selection["selected_research"] = [item for item in selection.get("selected_formal", []) if item.get("document_type") == "article"]
    selection["selected_reviews"] = [item for item in selection.get("selected_formal", []) if item.get("document_type") == "review"]
    return count
