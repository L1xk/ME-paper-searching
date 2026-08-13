from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Callable

from .http import request_bytes
from .io_utils import clean_text


Fetcher = Callable[..., bytes]


def _section_text(root: ET.Element, wanted: tuple[str, ...], limit: int = 12000) -> str:
    chunks: list[str] = []
    for sec in root.findall(".//sec"):
        title = clean_text(" ".join(sec.find("title").itertext())) if sec.find("title") is not None else ""
        if any(key in title.lower() for key in wanted):
            text = clean_text(" ".join(sec.itertext()))
            if text:
                chunks.append(text)
        if sum(map(len, chunks)) >= limit:
            break
    return clean_text(" ".join(chunks))[:limit]


def verify_candidate(record: dict[str, Any], timeout: int = 30, fetch_bytes: Fetcher = request_bytes) -> dict[str, Any]:
    result = dict(record)
    abstract = clean_text(record.get("abstract"))
    pmcid = re.sub(r"^PMC", "PMC", clean_text(record.get("pmcid")), flags=re.I)
    if pmcid:
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
        try:
            root = ET.fromstring(fetch_bytes(url, timeout=timeout))
            evidence = _section_text(root, ("result", "method", "experiment", "discussion", "conclusion"))
            if len(evidence) >= 500:
                result.update({"verification_level": "full_text", "evidence_text": evidence, "verification_note": "已核验 Europe PMC 开放全文的结果/方法等相关段落。", "fulltext_url": url})
                return result
        except Exception:
            pass
    if abstract:
        result.update({"verification_level": "abstract", "evidence_text": abstract, "verification_note": "未取得合法开放全文，已按降级规则仅核验公开摘要。"})
    else:
        result.update({"verification_level": "metadata", "evidence_text": "", "verification_note": "仅有元数据，不进入推荐。"})
    return result


def verify_all(records: list[dict[str, Any]], timeout: int = 30) -> list[dict[str, Any]]:
    return [verify_candidate(record, timeout=timeout) for record in records]

