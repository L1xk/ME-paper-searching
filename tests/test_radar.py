from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from me_protein_radar.config import RadarConfig
from me_protein_radar.deepseek import BudgetLedger, DeepSeekClient
from me_protein_radar.discovery import crossref_search, inverted_abstract, merge_candidates
from me_protein_radar.io_utils import RadarError
from me_protein_radar.render import render, subject
from me_protein_radar.pipeline import run
from me_protein_radar.selection import commit_history, select
from me_protein_radar.verification import verify_candidate


ROOT = Path(__file__).resolve().parents[1]


def enriched(index: int, published: str, *, review: bool = False, track: str = "metabolic_engineering", wet_lab: bool = True) -> dict:
    return {
        "title": f"Engineering microbial pathway {index}", "title_zh": f"微生物通路工程 {index}",
        "authors": [f"Author {index}"], "first_author": f"Author {index}", "journal": "Metabolic Engineering",
        "publication_date": published, "doi": f"10.9999/radar.{index}", "url": f"https://doi.org/10.9999/radar.{index}",
        "document_type": "review" if review else "article", "is_preprint": False, "track": track,
        "domain": "microbial", "cell_free_direct_support": False, "wet_lab": wet_lab,
        "host_or_object": "Escherichia coli", "product_or_task": "biosynthesis", "methods": ["pathway engineering"],
        "base_score": 84, "recommendation_reason_zh": "工程路径明确，证据充分。", "summary_zh": "研究构建并验证微生物合成通路，结果支持其工程价值。",
        "evidence_scope": "abstract", "uncertainty_note": "", "verification_level": "abstract", "verification_note": "公开摘要核验",
        "semantic_relevance_verified": True, "summary_model": "deepseek-v4-flash", "summary_validated": True, "source_labels": ["PubMed"], "top_journal": True,
    }


class DiscoveryTests(unittest.TestCase):
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

    def test_fulltext_then_abstract_fallback(self):
        xml = b"<article><body><sec><title>Results</title><p>" + b"validated enzyme activity " * 40 + b"</p></sec></body></article>"
        full = verify_candidate({"abstract": "public abstract", "pmcid": "PMC1"}, fetch_bytes=lambda *a, **k: xml)
        self.assertEqual(full["verification_level"], "full_text")
        fallback = verify_candidate({"abstract": "public abstract", "pmcid": "PMC1"}, fetch_bytes=lambda *a, **k: (_ for _ in ()).throw(OSError()))
        self.assertEqual(fallback["verification_level"], "abstract")


class DeepSeekTests(unittest.TestCase):
    def test_structured_result_and_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.json"
            config = RadarConfig.from_path(ROOT / "config" / "radar.json")
            ledger = BudgetLedger(path, 20, 1, 2, date(2026, 8, 13))
            result = {"eligible": True, "document_type": "article", "track": "integrated", "domain": "microbial", "cell_free_direct_support": False, "wet_lab": True, "host_or_object": "E. coli", "product_or_task": "product", "methods": ["ML", "assay"], "base_score": 90, "title_zh": "测试题目", "recommendation_reason_zh": "同时验证通路与酶改造。", "summary_zh": "研究使用模型筛选酶突变，并通过湿实验和微生物通路验证其作用。", "evidence_scope": "abstract", "uncertainty_note": ""}
            response = {"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
            client = DeepSeekClient(config, ledger, transport=lambda *a, **k: response, api_key="test-only")
            self.assertTrue(client.analyze({"title": "x", "evidence_text": "evidence", "verification_level": "abstract"})["eligible"])
            self.assertAlmostEqual(ledger.spent(), .002, places=6)

    def test_budget_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.json"
            path.write_text(json.dumps({"version": 1, "months": {"2026-08": {"estimated_cny": 20, "prompt_tokens": 0, "completion_tokens": 0}}}), encoding="utf-8")
            ledger = BudgetLedger(path, 20, 1, 2, date(2026, 8, 13))
            with self.assertRaises(RadarError): ledger.ensure_available(.001)


class SelectionTests(unittest.TestCase):
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
        self.assertNotIn("javascript", mail.lower())
        self.assertTrue(subject("test", "2026-08-17", 10).startswith("[TEST]"))
        self.assertEqual(history["recommended"], {})
        committed = commit_history(history, chosen, date(2026, 8, 17))
        self.assertEqual(len(committed["recommended"]), len(chosen["selected_formal"]))

    def test_historical_max_is_never_broken_to_fill(self):
        config = RadarConfig.from_path(ROOT / "config" / "radar.json")
        papers = [enriched(i, f"{2020 + i % 5}-03-12") for i in range(1, 15)]
        papers += [enriched(50, "2024-01-01", review=True), enriched(51, "2024-02-01", review=True)]
        with self.assertRaisesRegex(RadarError, "Formal quota unmet"):
            select(papers, {"recommended": {}}, date(2026, 8, 17), config)


class PipelineTests(unittest.TestCase):
    def test_history_changes_only_after_successful_production_send(self):
        papers = [enriched(i, f"{2020 + i % 5}-03-12") for i in range(1, 10)]
        papers += [enriched(20, "2026-08-01", review=True), enriched(21, "2026-08-02", review=True)]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            history = base / "history.json"
            history.write_text('{"version":1,"recommended":{}}', encoding="utf-8")
            args = Namespace(config=ROOT / "config" / "radar.json", history=history, usage=base / "usage.json", output=base / "output", issue_date="2026-08-17", mode="production", dry_run=False, candidates=None)
            with patch("me_protein_radar.pipeline.discover", return_value=(papers, [])), patch("me_protein_radar.pipeline.verify_all", side_effect=lambda rows, timeout: rows), patch("me_protein_radar.pipeline.DeepSeekClient", return_value=object()), patch("me_protein_radar.pipeline.analyze_all", return_value=(papers, [])), patch("me_protein_radar.pipeline.send_html") as sender:
                result = run(args)
            self.assertTrue(result["history_committed"])
            self.assertTrue(sender.called)
            self.assertGreaterEqual(len(json.loads(history.read_text(encoding="utf-8"))["recommended"]), 10)

            history.write_text('{"version":1,"recommended":{}}', encoding="utf-8")
            args.mode = "test"
            with patch("me_protein_radar.pipeline.discover", return_value=(papers, [])), patch("me_protein_radar.pipeline.verify_all", side_effect=lambda rows, timeout: rows), patch("me_protein_radar.pipeline.DeepSeekClient", return_value=object()), patch("me_protein_radar.pipeline.analyze_all", return_value=(papers, [])), patch("me_protein_radar.pipeline.send_html"):
                result = run(args)
            self.assertFalse(result["history_committed"])
            self.assertEqual(json.loads(history.read_text(encoding="utf-8"))["recommended"], {})


if __name__ == "__main__":
    unittest.main()
