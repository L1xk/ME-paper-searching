from __future__ import annotations

import hashlib
import html
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Callable, Iterable

from .config import RadarConfig
from .http import request_bytes, request_json
from .io_utils import RadarError, clean_text, first_nonempty, is_top_journal, normalize_doi, parse_date, rolling_years_before


Record = dict[str, Any]
Fetcher = Callable[..., Any]
TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(r"[a-z0-9]+")

POSITIVE_GROUPS = (
    {"metabolic", "pathway", "biosynthesis", "bioproduction", "synthetic", "microbial"},
    {"enzyme", "protein", "biocatalysis", "biocatalytic", "directed", "evolution"},
    {"learning", "artificial", "intelligence", "language", "model", "computational", "design"},
    {"yeast", "saccharomyces", "yarrowia", "pichia", "komagataella", "escherichia", "bacillus", "corynebacterium", "fungal", "bacterial"},
)
EXCLUDED_ONLY = {"mammalian", "mouse", "mice", "human", "plant", "arabidopsis", "animal"}
PREPRINT_TYPE_HINTS = {"posted-content", "preprint", "working-paper", "submitted"}
PREPRINT_MARKERS = (
    "arxiv", "biorxiv", "medrxiv", "chemrxiv", "research square",
    "preprints.org", "ssrn", "osf preprints",
)
PREPRINT_DOI_PREFIXES = ("10.1101/", "10.21203/rs.3.rs-", "10.26434/chemrxiv", "10.20944/preprints")
_LAST_ARXIV_REQUEST = 0.0


def strip_markup(value: Any) -> str:
    return clean_text(html.unescape(TAG_RE.sub(" ", str(value or ""))))


def date_parts(value: Any) -> str:
    if not isinstance(value, list) or not value or not isinstance(value[0], list):
        return ""
    parts = [int(x) for x in value[0][:3]]
    while len(parts) < 3:
        parts.append(1)
    try:
        return date(*parts).isoformat()
    except ValueError:
        return ""


def preprint_from_metadata(raw: Record, source: str = "") -> bool:
    if bool(raw.get("is_preprint")):
        return True
    hint = clean_text(raw.get("document_type_hint")).casefold()
    if hint in PREPRINT_TYPE_HINTS or "preprint" in hint or "posted" in hint:
        return True
    doi = normalize_doi(raw.get("doi"))
    if any(doi.startswith(prefix) for prefix in PREPRINT_DOI_PREFIXES):
        return True
    haystack = " ".join(
        clean_text(value).casefold()
        for value in (source, raw.get("journal"), raw.get("url"), raw.get("publisher"))
    )
    return any(marker in haystack for marker in PREPRINT_MARKERS)


def normalize_record(raw: Record, source: str) -> Record:
    title = strip_markup(raw.get("title"))
    abstract = strip_markup(raw.get("abstract"))
    authors = [clean_text(item) for item in (raw.get("authors") or []) if clean_text(item)]
    published = parse_date(raw.get("publication_date"))
    result: Record = {
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "first_author": authors[0] if authors else clean_text(raw.get("first_author")),
        "journal": clean_text(raw.get("journal")),
        "publication_date": published.isoformat() if published else "",
        "doi": normalize_doi(raw.get("doi")),
        "url": clean_text(raw.get("url")),
        "document_type_hint": clean_text(raw.get("document_type_hint")).lower(),
        "is_preprint": preprint_from_metadata(raw, source),
        "pmid": clean_text(raw.get("pmid")),
        "pmcid": clean_text(raw.get("pmcid")),
        "citation_count": int(raw.get("citation_count") or 0),
        "language": clean_text(raw.get("language") or "en").lower(),
        "source_labels": [source],
    }
    if not result["url"] and result["doi"]:
        result["url"] = "https://doi.org/" + result["doi"]
    return result


def record_key(record: Record) -> str:
    if record.get("doi"):
        return "doi:" + normalize_doi(record["doi"])
    title = " ".join(TOKEN_RE.findall(str(record.get("title", "")).lower()))
    author = str(record.get("first_author", "")).lower()
    return "title:" + hashlib.sha256(f"{author}|{title}".encode()).hexdigest()[:24]


def similarity(left: str, right: str) -> float:
    a = set(TOKEN_RE.findall(left.lower()))
    b = set(TOKEN_RE.findall(right.lower()))
    return len(a & b) / len(a | b) if a and b else 0.0


def merge_candidates(records: Iterable[Record]) -> list[Record]:
    merged: list[Record] = []
    for raw in records:
        if not raw.get("title"):
            continue
        match = next((item for item in merged if record_key(item) == record_key(raw) or (item.get("first_author") and item.get("first_author") == raw.get("first_author") and similarity(item["title"], raw["title"]) >= 0.9)), None)
        if match is None:
            merged.append(dict(raw))
            continue
        source_labels = sorted(set(match.get("source_labels", []) + raw.get("source_labels", [])))
        query_families = sorted(set(match.get("query_families", []) + raw.get("query_families", [])))
        match_preprint = bool(match.get("is_preprint"))
        raw_preprint = bool(raw.get("is_preprint"))
        if match_preprint and not raw_preprint:
            replacement = dict(raw)
            match.clear()
            match.update(replacement)
        match["source_labels"] = source_labels
        match["query_families"] = query_families
        for field in ("abstract", "doi", "url", "journal", "publication_date", "pmid", "pmcid", "document_type_hint", "authors"):
            if not match.get(field) and raw.get(field):
                match[field] = raw[field]
        match["is_preprint"] = match_preprint and raw_preprint
        match["citation_count"] = max(int(match.get("citation_count") or 0), int(raw.get("citation_count") or 0))
    return merged


def cheap_relevance(record: Record) -> float:
    text = f"{record.get('title', '')} {record.get('abstract', '')}".lower()
    tokens = set(TOKEN_RE.findall(text))
    group_hits = sum(bool(tokens & group) for group in POSITIVE_GROUPS)
    title_tokens = set(TOKEN_RE.findall(str(record.get("title", "")).lower()))
    title_bonus = sum(bool(title_tokens & group) for group in POSITIVE_GROUPS)
    excluded = bool(tokens & EXCLUDED_ONLY) and not bool(tokens & POSITIVE_GROUPS[3])
    return group_hits * 10 + title_bonus * 3 + min(int(record.get("citation_count") or 0), 100) / 25 - (30 if excluded else 0)


def crossref_search(query: str, from_date: date, until_date: date, rows: int, mailto: str, timeout: int, fetch: Fetcher = request_json) -> list[Record]:
    params = {"query": query, "filter": f"from-pub-date:{from_date},until-pub-date:{until_date}", "rows": rows, "select": "DOI,title,author,container-title,published,abstract,type,URL,is-referenced-by-count"}
    if mailto:
        params["mailto"] = mailto
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = fetch(url, timeout=timeout)
    output = []
    for item in data.get("message", {}).get("items", []):
        authors = [clean_text(" ".join(filter(None, (a.get("given"), a.get("family"))))) for a in item.get("author", [])]
        output.append(normalize_record({"title": first_nonempty(*(item.get("title") or [])), "abstract": item.get("abstract"), "authors": authors, "journal": first_nonempty(*(item.get("container-title") or [])), "publication_date": date_parts((item.get("published") or {}).get("date-parts")), "doi": item.get("DOI"), "url": item.get("URL"), "language": item.get("language"), "document_type_hint": item.get("type"), "citation_count": item.get("is-referenced-by-count")}, "Crossref"))
    return output


def pubmed_search(query: str, from_date: date, until_date: date, rows: int, mailto: str, timeout: int, fetch_bytes: Fetcher = request_bytes) -> list[Record]:
    term = f"({query}) AND ({from_date:%Y/%m/%d}[Date - Publication] : {until_date:%Y/%m/%d}[Date - Publication])"
    common = {"tool": "me_protein_radar"}
    if mailto:
        common["email"] = mailto
    ncbi_key = os.getenv("NCBI_API_KEY", "").strip()
    if ncbi_key:
        common["api_key"] = ncbi_key
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode({**common, "db": "pubmed", "retmode": "json", "retmax": rows, "term": term})
    search_data = request_json(search_url, timeout=timeout) if fetch_bytes is request_bytes else __import__("json").loads(fetch_bytes(search_url, timeout=timeout).decode())
    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode({**common, "db": "pubmed", "rettype": "abstract", "retmode": "xml", "id": ",".join(ids)})
    root = ET.fromstring(fetch_bytes(fetch_url, timeout=timeout))
    output = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        art = medline.find("Article") if medline is not None else None
        if medline is None or art is None:
            continue
        title = "".join(art.findtext("ArticleTitle", default=""))
        abstract = " ".join("".join(node.itertext()) for node in art.findall("Abstract/AbstractText"))
        authors = [clean_text(f"{node.findtext('ForeName', '')} {node.findtext('LastName', '')}") for node in art.findall("AuthorList/Author")]
        doi = ""
        pmcid = ""
        for node in article.findall("PubmedData/ArticleIdList/ArticleId"):
            if node.attrib.get("IdType") == "doi": doi = node.text or ""
            if node.attrib.get("IdType") == "pmc": pmcid = node.text or ""
        pubdate = art.find("Journal/JournalIssue/PubDate")
        year = pubdate.findtext("Year", "") if pubdate is not None else ""
        month = pubdate.findtext("Month", "1") if pubdate is not None else "1"
        day = pubdate.findtext("Day", "1") if pubdate is not None else "1"
        month_map = {name: index for index, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
        month = str(month_map.get(month[:3].title(), month))
        types = [node.text or "" for node in art.findall("PublicationTypeList/PublicationType")]
        type_hint = "preprint" if any("preprint" in t.lower() for t in types) else ("review" if any("review" in t.lower() for t in types) else "article")
        output.append(normalize_record({"title": title, "abstract": abstract, "authors": authors, "journal": art.findtext("Journal/Title", ""), "publication_date": f"{year}-{month}-{day}" if year else "", "doi": doi, "url": f"https://pubmed.ncbi.nlm.nih.gov/{medline.findtext('PMID', '')}/", "pmid": medline.findtext("PMID", ""), "pmcid": pmcid, "document_type_hint": type_hint}, "PubMed"))
    return output


def europe_pmc_search(query: str, from_date: date, until_date: date, rows: int, timeout: int, fetch: Fetcher = request_json) -> list[Record]:
    epmc_query = f"({query}) AND FIRST_PDATE:[{from_date.isoformat()} TO {until_date.isoformat()}]"
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode({"query": epmc_query, "format": "json", "pageSize": rows, "resultType": "core"})
    data = fetch(url, timeout=timeout)
    output = []
    for item in data.get("resultList", {}).get("result", []):
        authors = [clean_text(a.get("fullName")) for a in item.get("authorList", {}).get("author", [])]
        output.append(normalize_record({"title": item.get("title"), "abstract": item.get("abstractText"), "authors": authors, "journal": item.get("journalTitle"), "publication_date": first_nonempty(item.get("firstPublicationDate"), item.get("electronicPublicationDate")), "doi": item.get("doi"), "url": f"https://europepmc.org/article/{item.get('source', '')}/{item.get('id', '')}", "pmid": item.get("pmid"), "pmcid": item.get("pmcid"), "document_type_hint": item.get("pubType"), "citation_count": item.get("citedByCount")}, "Europe PMC"))
    return output


def inverted_abstract(value: Any) -> str:
    if not isinstance(value, dict): return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        if isinstance(positions, list): positioned.extend((int(position), str(word)) for position in positions)
    return " ".join(word for _, word in sorted(positioned))


def openalex_search(query: str, from_date: date, until_date: date, rows: int, mailto: str, timeout: int, api_key: str = "", fetch: Fetcher = request_json) -> list[Record]:
    if not api_key:
        raise RadarError("OPENALEX_API_KEY is not configured (OpenAlex requires a free key since 2026-02-13)")
    params = {"search": query, "filter": f"from_publication_date:{from_date},to_publication_date:{until_date}", "per-page": min(rows, 100), "select": "id,doi,title,publication_date,authorships,primary_location,type,cited_by_count,open_access,abstract_inverted_index", "api_key": api_key}
    if mailto: params["mailto"] = mailto
    data = fetch("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
    output = []
    for item in data.get("results", []):
        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        authors = [clean_text((a.get("author") or {}).get("display_name")) for a in item.get("authorships", [])]
        output.append(normalize_record({"title": item.get("title"), "abstract": inverted_abstract(item.get("abstract_inverted_index")), "authors": authors, "journal": source.get("display_name"), "publication_date": item.get("publication_date"), "doi": item.get("doi"), "url": location.get("landing_page_url") or item.get("id"), "document_type_hint": item.get("type"), "citation_count": item.get("cited_by_count")}, "OpenAlex"))
    return output


def arxiv_search(query: str, from_date: date, until_date: date, rows: int, timeout: int, fetch_bytes: Fetcher = request_bytes) -> list[Record]:
    global _LAST_ARXIV_REQUEST
    tokens = [token.lower() for token in TOKEN_RE.findall(query) if token.lower() not in {"and", "or", "the"}]
    tokens = list(dict.fromkeys(tokens))[:12]
    search = " OR ".join(f"all:{token}" for token in tokens)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": search, "start": 0, "max_results": rows, "sortBy": "submittedDate", "sortOrder": "descending"})
    wait = 3.0 - (time.monotonic() - _LAST_ARXIV_REQUEST)
    if wait > 0: time.sleep(wait)
    root = ET.fromstring(fetch_bytes(url, timeout=timeout))
    _LAST_ARXIV_REQUEST = time.monotonic()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    output = []
    for entry in root.findall("a:entry", ns):
        published = entry.findtext("a:published", "", ns)
        day = parse_date(published)
        if not day or not from_date <= day <= until_date: continue
        authors = [node.findtext("a:name", "", ns) for node in entry.findall("a:author", ns)]
        output.append(normalize_record({"title": entry.findtext("a:title", "", ns), "abstract": entry.findtext("a:summary", "", ns), "authors": authors, "journal": "arXiv", "publication_date": published, "url": entry.findtext("a:id", "", ns), "document_type_hint": "preprint", "is_preprint": True}, "arXiv"))
    return output


def biorxiv_recent(from_date: date, until_date: date, rows: int, timeout: int, fetch: Fetcher = request_json) -> list[Record]:
    start = max(from_date, until_date - timedelta(days=45))
    url = f"https://api.biorxiv.org/details/biorxiv/{start.isoformat()}/{until_date.isoformat()}/0/json"
    data = fetch(url, timeout=timeout)
    output = []
    for item in data.get("collection", [])[:rows * 4]:
        output.append(normalize_record({"title": item.get("title"), "abstract": item.get("abstract"), "authors": [x.strip() for x in str(item.get("authors", "")).split(";")], "journal": "bioRxiv", "publication_date": item.get("date"), "doi": item.get("doi"), "url": f"https://doi.org/{item.get('doi')}", "document_type_hint": "preprint", "is_preprint": True}, "bioRxiv"))
    return sorted(output, key=cheap_relevance, reverse=True)[:rows]


def discover(config: RadarConfig, issue_date: date, mailto: str) -> tuple[list[Record], list[str]]:
    settings = config.discovery
    rows = int(settings.get("rows_per_query", 20))
    timeout = int(settings.get("request_timeout_seconds", 30))
    lower = rolling_years_before(issue_date, int(config.get("rolling_years", 6)))
    sources = set(settings.get("sources", []))
    records: list[Record] = []
    warnings: list[str] = []
    openalex_key = os.getenv("OPENALEX_API_KEY", "").strip()
    adapters = {
        "crossref": lambda q: crossref_search(q, lower, issue_date, rows, mailto, timeout),
        "pubmed": lambda q: pubmed_search(q, lower, issue_date, rows, mailto, timeout),
        "europe_pmc": lambda q: europe_pmc_search(q, lower, issue_date, rows, timeout),
        "openalex": lambda q: openalex_search(q, lower, issue_date, rows, mailto, timeout, openalex_key),
        "arxiv": lambda q: arxiv_search(q, lower, issue_date, rows, timeout),
    }
    if "openalex" in sources and not openalex_key:
        warnings.append("openalex: OPENALEX_API_KEY 未配置，已跳过该来源")
        adapters.pop("openalex", None)
    for family in settings.get("query_families", []):
        query = family.get("query", "")
        for name, adapter in adapters.items():
            if name not in sources: continue
            try:
                found = adapter(query)
                for record in found:
                    record["query_families"] = [str(family.get("name", "query"))]
                records.extend(found)
            except Exception as exc:
                warnings.append(f"{name}/{family.get('name', 'query')}: {type(exc).__name__}: {exc}")
    if "biorxiv" in sources:
        try:
            records.extend(biorxiv_recent(lower, issue_date, rows, timeout))
        except Exception as exc:
            warnings.append(f"biorxiv/recent: {type(exc).__name__}: {exc}")
    candidates = merge_candidates(records)
    candidates = [item for item in candidates if item.get("abstract") and cheap_relevance(item) >= 16]
    candidates.sort(key=lambda item: (cheap_relevance(item), int(item.get("citation_count") or 0)), reverse=True)
    limit = int(config.get("max_semantic_candidates", 80))
    preprint_limit = min(int(config.get("max_preprint_semantic_candidates", 12)), limit)
    recent_cutoff = issue_date - timedelta(days=int(config.get("recent_days", 30)))
    formal_candidates = [x for x in candidates if not x.get("is_preprint")]
    preprint_candidates = [x for x in candidates if x.get("is_preprint")]
    reserved_preprints = preprint_candidates[:preprint_limit]
    formal_limit = max(0, limit - len(reserved_preprints))
    top_names = list(config.get("top_journals", []))
    top_aliases = list(config.get("top_journal_aliases", []))
    top_formal = [x for x in formal_candidates if is_top_journal(x.get("journal"), top_names, top_aliases)]
    reviews = [x for x in formal_candidates if "review" in x.get("query_families", []) or "review" in str(x.get("document_type_hint", ""))]
    recent = [x for x in formal_candidates if (parse_date(x.get("publication_date")) or lower) >= recent_cutoff and x not in reviews]
    historical = [x for x in formal_candidates if x not in reviews and x not in recent]
    reserved: list[Record] = []
    for pool, quota in ((top_formal, 25), (reviews, 20), (recent, 20), (historical, formal_limit)):
        for item in pool:
            if item not in reserved and len(reserved) < formal_limit and sum(x in pool for x in reserved) < quota:
                reserved.append(item)
    if len(reserved) < formal_limit:
        reserved += [x for x in formal_candidates if x not in reserved][: formal_limit - len(reserved)]
    return reserved[:formal_limit] + reserved_preprints, warnings
