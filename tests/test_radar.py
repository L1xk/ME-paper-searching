from __future__ import annotations

import http.client
import json
import tempfile
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from me_protein_radar.config import RadarConfig
from me_protein_radar.deepseek import BudgetLedger, DeepSeekClient, screen_all, summarize_selected
from me_protein_radar.delivery import delivery_period, history_delivery_records, load_delivery_state, mark_delivered, update_automation_status, was_delivered
from me_protein_radar.discovery import build_review_search_specs, build_targeted_search_specs, crossref_search, hard_exclusion_reason, inverted_abstract, merge_candidates, normalize_record
from me_protein_radar.io_utils import RadarError, assess_journal, is_top_journal
from me_protein_radar.http import request_bytes
from me_protein_radar.render import render, subject
from me_protein_radar.pipeline import run
from me_protein_radar.selection import commit_history, select
from me_protein_radar.verification import verify_candidate


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def enriched(index: int, published: str, *, review: bool = False, track: str = "metabolic_engineering", wet_lab: bool = True, preprint: bool = False, top: bool = True, exceptional: bool = False, base_score: int = 84, journal: str = "Metabolic Engineering") -> dict:
    return {
        "title": f"Engineering microbial pathway {index}", "title_zh": f"微生物通路工程 {index}",
        "authors": [f"Author {index}"], "first_author": f"Author {index}", "journal": journal, "canonical_journal": journal,
        "publication_date": published, "doi": f"10.9999/radar.{index}", "url": f"https://doi.org/10.9999/radar.{index}",
        "document_type": "preprint" if preprint else ("review" if review else "article"), "is_preprint": preprint, "track": track,
        "domain": "microbial", "cell_free_direct_support": False, "wet_lab": wet_lab,
        "host_or_object": "Escherichia coli", "product_or_task": "biosynthesis", "methods": ["pathway engineering"],
        "base_score": base_score, "exceptional_novelty": exceptional, "novelty_category": "new_complete_pathway" if exceptional else "none", "novelty_evidence_zh": "证据明确支持首次完整新通路并完成实验验证。" if exceptional else "", "recommendation_reason_zh": "工程路径明确，证据充分。", "summary_zh": "研究构建并验证微生物合成通路，结果支持其工程价值。",
        "evidence_scope": "abstract", "uncertainty_note": "", "verification_level": "abstract", "verification_note": "公开摘要核验",
        "semantic_relevance_verified": True, "summary_model": "deepseek-v4-flash", "summary_validated": True, "source_labels": ["bioRxiv" if preprint else "PubMed"], "top_journal": top,
    }


class DiscoveryTests(unittest.TestCase):
    def test_incomplete_http_read_is_retried_and_query_is_redacted(self):
        responses = iter([
            _Response(http.client.IncompleteRead(b"partial")),
            _Response(b'{"ok": true}'),
        ])
        with patch("me_protein_radar.http.urllib.request.urlopen", side_effect=lambda *a, **k: next(responses)) as opener, patch("me_protein_radar.http.time.sleep") as sleeper:
            payload = request_bytes("https://example.org/api?api_key=secret", retries=2)
        self.assertEqual(payload, b'{"ok": true}')
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once()

        with patch("me_protein_radar.http.urllib.request.urlopen", return_value=_Response(http.client.IncompleteRead(b""))), patch("me_protein_radar.http.time.sleep"):
            with self.assertRaises(RadarError) as raised:
                request_bytes("https://example.org/api?api_key=secret", retries=1)
        self.assertNotIn("secret", str(raised.exception))
        self.assertIn("IncompleteRead", str(raised.exception))

    def test_crossref_normalization_and_dedup(self):
        response = {"message": {"items": [{"DOI": "10.1/X", "title": ["Microbial pathway engineering"], "author": [{"given": "A", "family": "Li"}], "container-title": ["Journal"], "published": {"date-parts": [[2026, 7, 1]]}, "abstract": "<jats:p>Enzyme and microbial biosynthesis.</jats:p>", "type": "journal-article", "URL": "https://doi.org/10.1/X"}]}}
        rows = crossref_search("query", date(2020, 1, 1), date(2026, 8, 1), 10, "a@example.com", 3, fetch=lambda *a, **k: response)
        self.assertEqual(rows[0]["doi"], "10.1/x")
        self.assertEqual(rows[0]["abstract"], "Enzyme and microbial biosynthesis.")
        duplicate = dict(rows[0]); duplicate["source_labels"] = ["OpenAlex"]
        merged = merge_candidates([rows[0], duplicate])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_labels"], ["Crossref", "OpenAlex"])
        self.assertEqual(inverted_abstract({"enzyme": [1], "engineered": [2], "We": [0]}), "We enzyme engineered")

    def test_crossref_preprint_detection_and_formal_upgrade(self):
        response = {"message": {"items": [{"DOI": "10.1101/2026.01.01.1", "title": ["AI-guided microbial enzyme engineering"], "author": [{"given": "A", "family": "Li"}], "container-title": ["bioRxiv"], "published": {"date-parts": [[2026, 7, 1]]}, "abstract": "Microbial enzyme design and wet-lab validation.", "type": "posted-content", "URL": "https://biorxiv.org/content/1"}]}}
        preprint = crossref_search("query", date(2020, 1, 1), date(2026, 8, 1), 10, "a@example.com", 3, fetch=lambda *a, **k: response)[0]
        self.assertTrue(preprint["is_preprint"])
        formal = dict(preprint, doi="10.9999/formal.1", journal="Nature Communications", url="https://doi.org/10.9999/formal.1", document_type_hint="journal-article", is_preprint=False, source_labels=["PubMed"])
        merged = merge_candidates([preprint, formal])
        self.assertEqual(len(merged), 1)
        self.assertFalse(merged[0]["is_preprint"])
        self.assertEqual(merged[0]["journal"], "Nature Communications")
        pubmed_preprint = normalize_record({"title": "x", "journal": "PubMed", "document_type_hint": "Preprint"}, "PubMed")
        self.assertTrue(pubmed_preprint["is_preprint"])
        self.assertTrue(is_top_journal("Nat Commun", ["Nature Communications"], ["Nat Commun"]))

    def test_targeted_queries_and_conditional_journal_policy(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        pubmed_specs = build_targeted_search_specs("pubmed", config)
        crossref_specs = build_targeted_search_specs("crossref", config)
        self.assertTrue(any("[Journal]" in item["query"] for item in pubmed_specs))
        self.assertTrue(any(item["journal"] == "ACS Catalysis" for item in crossref_specs))
        off_scope = assess_journal("J Agric Food Chem", config.journals, "Protein language model for antibody design", "computational prediction", "article")
        in_scope = assess_journal("J Agric Food Chem", config.journals, "Enzyme engineering for food flavor biosynthesis", "microbial fermentation and biocatalysis", "article")
        self.assertFalse(off_scope["top_journal"])
        self.assertTrue(in_scope["top_journal"])
        self.assertEqual(in_scope["canonical_name"], "Journal of Agricultural and Food Chemistry")
        self.assertFalse(assess_journal("Trends Biotechnol", config.journals, document_type="article")["top_journal"])
        self.assertTrue(assess_journal("Trends Biotechnol", config.journals, document_type="review")["top_journal"])
        self.assertEqual(hard_exclusion_reason({"title": "Metal nanozyme for tumor therapy"}), "nanozyme")

    def test_review_queries_use_source_native_type_filters(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        pubmed_specs = build_review_search_specs("pubmed", config)
        epmc_specs = build_review_search_specs("europe_pmc", config)
        crossref_specs = build_review_search_specs("crossref", config)
        self.assertGreaterEqual(len(pubmed_specs), 3)
        self.assertTrue(all("Review[Publication Type]" in item["query"] for item in pubmed_specs))
        self.assertTrue(all("PUB_TYPE:review" in item["query"] for item in epmc_specs))
        self.assertTrue(all(item["review_search"] for item in crossref_specs))

    def test_review_search_provenance_survives_deduplication(self):
        first = normalize_record({"title": "Review of microbial metabolic engineering", "doi": "10.1/review", "abstract": "Microbial pathway and enzyme engineering."}, "Crossref")
        second = dict(first, source_labels=["PubMed"], query_families=["review_focus_metabolic"], review_search_hit=True)
        merged = merge_candidates([first, second])
        self.assertTrue(merged[0]["review_search_hit"])
        self.assertIn("review_focus_metabolic", merged[0]["query_families"])

    def test_fulltext_then_abstract_fallback(self):
        xml = b"<article><body><sec><title>Results</title><p>" + b"validated enzyme activity " * 40 + b"</p></sec></body></article>"
        full = verify_candidate({"abstract": "public abstract", "pmcid": "PMC1"}, fetch_bytes=lambda *a, **k: xml)
        self.assertEqual(full["verification_level"], "full_text")
        fallback = verify_candidate({"abstract": "public abstract", "pmcid": "PMC1"}, fetch_bytes=lambda *a, **k: (_ for _ in ()).throw(OSError()))
        self.assertEqual(fallback["verification_level"], "abstract")


class DeepSeekTests(unittest.TestCase):
    def test_two_stage_structured_results_and_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.json"
            config = RadarConfig.from_path(ROOT / "config" / "radar.json")
            ledger = BudgetLedger(path, 20, 1, 2, date(2026, 8, 13))
            screen_result = {"eligible": True, "document_type": "article", "track": "integrated", "domain": "microbial", "cell_free_direct_support": False, "wet_lab": True, "host_or_object": "E. coli", "product_or_task": "product", "methods": ["ML", "assay"], "base_score": 90, "exceptional_novelty": False, "novelty_category": "none", "novelty_evidence_zh": "", "evidence_scope": "abstract", "uncertainty_note": ""}
            summary_result = {"title_zh": "测试题目", "recommendation_reason_zh": "同时验证通路与酶改造。", "summary_zh": "研究使用模型筛选酶突变，并通过湿实验和微生物通路验证其作用。"}
            responses = iter([screen_result, summary_result])
            def transport(*args, **kwargs):
                value = next(responses)
                return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
            client = DeepSeekClient(config, ledger, transport=transport, api_key="test-only")
            self.assertTrue(client.screen({"title": "x", "evidence_text": "evidence", "verification_level": "abstract"})["eligible"])
            self.assertEqual(client.summarize({"title": "x", "evidence_text": "evidence"})["title_zh"], "测试题目")
            self.assertAlmostEqual(ledger.spent(), .004, places=6)

    def test_budget_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.json"
            path.write_text(json.dumps({"version": 1, "months": {"2026-08": {"estimated_cny": 20, "prompt_tokens": 0, "completion_tokens": 0}}}), encoding="utf-8")
            ledger = BudgetLedger(path, 20, 1, 2, date(2026, 8, 13))
            with self.assertRaises(RadarError): ledger.ensure_available(.001)

    def test_model_preprint_decision_is_conservative(self):
        result = {"eligible": True, "document_type": "preprint", "track": "integrated", "domain": "microbial", "cell_free_direct_support": False, "wet_lab": True, "host_or_object": "E. coli", "product_or_task": "product", "methods": ["assay"], "base_score": 90, "exceptional_novelty": False, "novelty_category": "none", "novelty_evidence_zh": "", "evidence_scope": "abstract", "uncertainty_note": ""}
        client = type("Client", (), {"settings": {"model": "deepseek-v4-flash"}, "screen": lambda self, record: result})()
        record = enriched(99, "2026-08-01", top=False)
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        accepted, rejected = screen_all([record], client, config.journals)
        self.assertFalse(rejected)
        self.assertTrue(accepted[0]["is_preprint"])
        self.assertEqual(accepted[0]["document_type"], "preprint")

    def test_single_screen_failure_is_isolated_but_outage_stops(self):
        accepted_result = {"eligible": True, "document_type": "article", "track": "integrated", "domain": "microbial", "cell_free_direct_support": False, "wet_lab": True, "host_or_object": "E. coli", "product_or_task": "product", "methods": ["assay"], "base_score": 90, "exceptional_novelty": False, "novelty_category": "none", "novelty_evidence_zh": "", "evidence_scope": "abstract", "uncertainty_note": ""}
        responses = iter([RadarError("temporary read failure"), accepted_result])

        def screen_once(_self, _record):
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value

        client = type("Client", (), {"settings": {"model": "deepseek-v4-flash"}, "screen": screen_once})()
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        first, second = enriched(90, "2026-08-01"), enriched(91, "2026-08-02")
        accepted, rejected = screen_all([first, second], client, config.journals, max_consecutive_failures=3)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertIn("model screening failed", rejected[0]["reason"])

        failed_client = type("Client", (), {"settings": {"model": "deepseek-v4-flash"}, "screen": lambda self, record: (_ for _ in ()).throw(RadarError("offline"))})()
        with self.assertRaisesRegex(RadarError, "2 consecutive candidates"):
            screen_all([first, second], failed_client, config.journals, max_consecutive_failures=2)

    def test_only_final_selection_receives_long_summaries(self):
        calls = []
        summary = {"title_zh": "中文题目", "recommendation_reason_zh": "值得阅读。", "summary_zh": "这是仅为最终入选论文生成的中文速读摘要。"}
        client = type("Client", (), {"settings": {"model": "deepseek-v4-flash"}, "summarize": lambda self, record: calls.append(record["title"]) or summary})()
        selection = {
            "selected_formal": [enriched(1, "2022-01-01"), enriched(2, "2026-08-01", review=True)],
            "selected_research": [],
            "selected_reviews": [],
        }
        self.assertEqual(summarize_selected(selection, client), 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(item["summary_validated"] for item in selection["selected_formal"]))
        self.assertEqual(len(selection["selected_research"]), 1)
        self.assertEqual(len(selection["selected_reviews"]), 1)


class SelectionTests(unittest.TestCase):
    def test_eight_qualified_papers_are_deliverable(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        papers = [enriched(i, "2022-03-12") for i in range(1, 7)]
        papers += [
            enriched(20, "2022-04-01", review=True, journal="Nature Reviews Chemistry"),
            enriched(21, "2022-04-02", review=True, journal="Trends in Biotechnology"),
        ]

        chosen = select(papers, {"recommended": {}}, date(2026, 8, 17), config)

        self.assertEqual(len(chosen["selected_formal"]), 8)
        self.assertEqual(len(chosen["selected_reviews"]), 2)
        self.assertEqual(sum(bool(x["top_journal"]) for x in chosen["selected_formal"]), 8)

    def test_nine_qualified_papers_are_deliverable(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        papers = [enriched(i, "2022-03-12") for i in range(1, 8)]
        papers += [
            enriched(20, "2026-08-01", review=True, journal="Nature Reviews Chemistry"),
            enriched(21, "2026-08-02", review=True, journal="Trends in Biotechnology"),
        ]

        chosen = select(papers, {"recommended": {}}, date(2026, 8, 17), config)

        self.assertEqual(len(chosen["selected_formal"]), 9)
        self.assertEqual(len(chosen["selected_reviews"]), 2)
        self.assertEqual(sum(x["age_pool"] == "historical" for x in chosen["selected_formal"]), 7)
        self.assertEqual(sum(bool(x["top_journal"]) for x in chosen["selected_formal"]), 9)

    def test_quotas_render_and_history_transaction(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        papers = [enriched(i, f"{2020 + i % 5}-03-12") for i in range(1, 10)]
        papers += [enriched(20, "2026-08-01", review=True), enriched(21, "2026-08-02", review=True)]
        papers += [enriched(i, "2026-08-05") for i in range(30, 34)]
        history = {"version": 1, "recommended": {}}
        chosen = select(papers, history, date(2026, 8, 17), config)
        self.assertGreaterEqual(len(chosen["selected_formal"]), 10)
        self.assertGreaterEqual(len(chosen["selected_reviews"]), 2)
        historical = [x for x in chosen["selected_formal"] if x["age_pool"] == "historical"]
        self.assertGreaterEqual(len(historical), 7)
        self.assertLessEqual(len(historical), 8)
        mail = render(chosen)
        self.assertIn("ME × Protein", mail)
        self.assertIn("科研视角 · Reviews", mail)
        self.assertNotIn("前沿预警", mail)
        self.assertNotIn("javascript", mail.lower())
        self.assertTrue(subject("test", "2026-08-17", 10).startswith("[TEST]"))
        self.assertEqual(history["recommended"], {})
        committed = commit_history(history, chosen, date(2026, 8, 17))
        self.assertEqual(len(committed["recommended"]), len(chosen["selected_formal"]))

    def test_preprints_never_enter_formal_and_are_capped(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        papers = [enriched(i, f"{2020 + i % 5}-03-12") for i in range(1, 10)]
        papers += [enriched(20, "2026-08-01", review=True), enriched(21, "2026-08-02", review=True)]
        papers += [enriched(i, "2026-08-05", preprint=True, top=False) for i in range(30, 40)]
        chosen = select(papers, {"recommended": {}}, date(2026, 8, 17), config)
        self.assertFalse(any(x["is_preprint"] for x in chosen["selected_formal"]))
        self.assertEqual(chosen["preprint_watchlist"], [])

    def test_only_verified_exceptional_non_top_can_fill(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        config = RadarConfig({**config.raw, "formal_min": 10}, config.path, config.journals)
        papers = [enriched(i, "2022-03-12") for i in range(1, 7)]
        papers += [enriched(20, "2023-08-01", review=True), enriched(21, "2023-08-02", review=True)]
        papers += [enriched(30, "2026-08-05", top=False, exceptional=True, base_score=96), enriched(31, "2026-08-06", top=False, exceptional=True, base_score=95)]
        chosen = select(papers, {"recommended": {}}, date(2026, 8, 17), config)
        self.assertEqual(sum(x["top_journal"] for x in chosen["selected_formal"]), 8)
        exceptions = [x for x in chosen["selected_formal"] if x["quality_tier"] == "exceptional_non_top"]
        self.assertEqual(len(exceptions), 2)
        self.assertTrue(all(x["novelty_evidence_zh"] for x in exceptions))
        exception_mail = render(chosen)
        self.assertIn("创新例外 · 非 Top 期刊", exception_mail)
        self.assertIn("创新例外依据", exception_mail)

        papers[-1] = enriched(31, "2026-08-06", top=False, exceptional=False, base_score=98)
        with self.assertRaisesRegex(RadarError, "Formal quota unmet"):
            select(papers, {"recommended": {}}, date(2026, 8, 17), config)

    def test_zero_top_journals_blocks_delivery(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        papers = [enriched(i, f"{2020 + i % 5}-03-12", top=False) for i in range(1, 10)]
        papers += [enriched(20, "2026-08-01", review=True, top=False), enriched(21, "2026-08-02", review=True, top=False)]
        with self.assertRaises(RadarError):
            select(papers, {"recommended": {}}, date(2026, 8, 17), config)

    def test_historical_max_is_never_broken_to_fill(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        config = RadarConfig({**config.raw, "formal_min": 10}, config.path, config.journals)
        papers = [enriched(i, f"{2020 + i % 5}-03-12") for i in range(1, 15)]
        papers += [enriched(50, "2024-01-01", review=True), enriched(51, "2024-02-01", review=True)]
        with self.assertRaisesRegex(RadarError, "Formal quota unmet"):
            select(papers, {"recommended": {}}, date(2026, 8, 17), config)

    def test_diversity_limits_apply_when_candidate_pool_allows(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        journals = ["Metabolic Engineering", "ACS Catalysis", "Nature Communications", "Nature Biotechnology", "JACS"]
        tracks = ["integrated", "metabolic_engineering", "enzyme_engineering"]
        papers = [enriched(i, "2022-03-12", journal=journals[i % len(journals)], track=tracks[i % len(tracks)]) for i in range(1, 11)]
        papers += [enriched(30, "2026-08-01", review=True, journal="Nature Reviews Chemistry"), enriched(31, "2026-08-02", review=True, journal="Trends in Biotechnology")]
        papers += [enriched(40, "2026-08-05", journal="Advanced Science", track="integrated"), enriched(41, "2026-08-06", journal="Cell Systems", track="ai_protein")]
        chosen = select(papers, {"recommended": {}}, date(2026, 8, 17), config)
        self.assertLessEqual(max(chosen["diversity_summary"]["journal_counts"].values()), 2)
        self.assertLessEqual(max(chosen["diversity_summary"]["track_counts"].values()), 4)
        self.assertEqual(chosen["diversity_summary"]["relaxations"], [])


class PipelineTests(unittest.TestCase):
    def test_nine_qualified_papers_are_sent_and_committed(self):
        papers = [enriched(i, "2022-03-12") for i in range(1, 8)]
        papers += [
            enriched(20, "2026-08-01", review=True, journal="Nature Reviews Chemistry"),
            enriched(21, "2026-08-02", review=True, journal="Trends in Biotechnology"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            history = base / "history.json"
            history.write_text('{"version":1,"recommended":{}}', encoding="utf-8")
            args = Namespace(
                config=ROOT / "config" / "radar.json",
                history=history,
                usage=base / "usage.json",
                delivery_state=base / "delivery_state.json",
                automation_status=base / "automation_status.json",
                output=base / "output",
                issue_date="2026-08-17",
                mode="production",
                dry_run=False,
                candidates=None,
            )
            with patch("me_protein_radar.pipeline.discover", return_value=(papers, [])), patch(
                "me_protein_radar.pipeline.verify_all", side_effect=lambda rows, timeout: rows
            ), patch("me_protein_radar.pipeline.DeepSeekClient", return_value=object()), patch(
                "me_protein_radar.pipeline.screen_all", return_value=(papers, [])
            ), patch(
                "me_protein_radar.pipeline.summarize_selected",
                side_effect=lambda selection, client: len(selection["selected_formal"]),
            ), patch("me_protein_radar.pipeline.send_html") as sender:
                result = run(args)

            self.assertEqual(result["formal"], 9)
            self.assertTrue(result["sent"])
            self.assertTrue(result["history_committed"])
            sender.assert_called_once()
            self.assertEqual(len(json.loads(history.read_text(encoding="utf-8"))["recommended"]), 9)

    def test_history_changes_only_after_successful_production_send(self):
        papers = [enriched(i, f"{2020 + i % 5}-03-12") for i in range(1, 10)]
        papers += [enriched(20, "2026-08-01", review=True), enriched(21, "2026-08-02", review=True)]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            history = base / "history.json"
            history.write_text('{"version":1,"recommended":{}}', encoding="utf-8")
            delivery_state = base / "delivery_state.json"
            automation_status = base / "automation_status.json"
            args = Namespace(config=ROOT / "config" / "radar.json", history=history, usage=base / "usage.json", delivery_state=delivery_state, automation_status=automation_status, output=base / "output", issue_date="2026-08-17", mode="production", dry_run=False, candidates=None)
            with patch("me_protein_radar.pipeline.discover", return_value=(papers, [])), patch("me_protein_radar.pipeline.verify_all", side_effect=lambda rows, timeout: rows), patch("me_protein_radar.pipeline.DeepSeekClient", return_value=object()), patch("me_protein_radar.pipeline.screen_all", return_value=(papers, [])), patch("me_protein_radar.pipeline.summarize_selected", side_effect=lambda selection, client: len(selection["selected_formal"])), patch("me_protein_radar.pipeline.send_html") as sender:
                result = run(args)
            self.assertTrue(result["history_committed"])
            self.assertTrue(sender.called)
            self.assertGreaterEqual(len(json.loads(history.read_text(encoding="utf-8"))["recommended"]), 10)
            state_after_send = json.loads(delivery_state.read_text(encoding="utf-8"))
            self.assertTrue(was_delivered(state_after_send, "2026-W34"))
            status_after_send = json.loads(automation_status.read_text(encoding="utf-8"))
            self.assertEqual(status_after_send["last_success"]["outcome"], "sent")

            delivery_state.unlink()
            with patch("me_protein_radar.pipeline.discover", side_effect=AssertionError("paid pipeline must not run twice")), patch("me_protein_radar.pipeline.send_html") as duplicate_sender:
                duplicate = run(args)
            self.assertTrue(duplicate["skipped"])
            self.assertEqual(duplicate["budget_cny"], 0.0)
            duplicate_sender.assert_not_called()
            recovered_state = json.loads(delivery_state.read_text(encoding="utf-8"))
            self.assertEqual(recovered_state["deliveries"]["2026-W34"]["source"], "history_recovery")

            history.write_text('{"version":1,"recommended":{}}', encoding="utf-8")
            args.mode = "test"
            state_before_test = delivery_state.read_text(encoding="utf-8")
            with patch("me_protein_radar.pipeline.discover", return_value=(papers, [])), patch("me_protein_radar.pipeline.verify_all", side_effect=lambda rows, timeout: rows), patch("me_protein_radar.pipeline.DeepSeekClient", return_value=object()), patch("me_protein_radar.pipeline.screen_all", return_value=(papers, [])), patch("me_protein_radar.pipeline.summarize_selected", side_effect=lambda selection, client: len(selection["selected_formal"])), patch("me_protein_radar.pipeline.send_html"):
                result = run(args)
            self.assertFalse(result["history_committed"])
            self.assertEqual(json.loads(history.read_text(encoding="utf-8"))["recommended"], {})
            self.assertEqual(delivery_state.read_text(encoding="utf-8"), state_before_test)


class DeliveryStateTests(unittest.TestCase):
    def test_iso_week_key_and_round_trip(self):
        self.assertEqual(delivery_period(date(2026, 8, 17)), "2026-W34")
        self.assertEqual(delivery_period(date(2026, 8, 23)), "2026-W34")
        self.assertEqual(delivery_period(date(2026, 8, 24)), "2026-W35")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "delivery.json"
            state = load_delivery_state(path)
            self.assertFalse(was_delivered(state, "2026-W34"))
            state = mark_delivered(
                state,
                "2026-W34",
                issue_date="2026-08-17",
                sent_at="2026-08-17T11:00:00+08:00",
                formal_count=12,
                review_count=2,
                subject="ME × Protein 周报",
            )
            self.assertTrue(was_delivered(state, "2026-W34"))
            self.assertFalse(was_delivered(state, "2026-W35"))

    def test_noop_health_check_preserves_last_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "automation.json"
            update_automation_status(
                path,
                checked_at="2026-08-17T11:00:00+08:00",
                issue_date="2026-08-17",
                period="2026-W34",
                mode="production",
                outcome="sent",
                trigger="schedule",
                run_id="100",
            )
            update_automation_status(
                path,
                checked_at="2026-08-18T10:46:00+08:00",
                issue_date="2026-08-18",
                period="2026-W34",
                mode="production",
                outcome="already_delivered",
                trigger="schedule",
                run_id="101",
            )
            status = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(status["last_check"]["outcome"], "already_delivered")
            self.assertEqual(status["last_success"]["outcome"], "sent")
            self.assertEqual(status["last_success"]["run_id"], "100")

    def test_history_can_recover_a_missing_week_lock(self):
        history = {
            "version": 1,
            "recommended": {
                "doi:one": {"issue_date": "2026-08-17", "title": "One"},
                "doi:old": {"issue_date": "2026-08-10", "title": "Old"},
                "doi:invalid": {"issue_date": "unknown", "title": "Invalid"},
            },
        }
        records = history_delivery_records(history, "2026-W34")
        self.assertEqual([item["title"] for item in records], ["One"])


if __name__ == "__main__":
    unittest.main()
