---
name: me-protein-paper-radar
description: Build, configure, test, audit, or run a weekly English-literature radar for microbial metabolic engineering, enzyme engineering, and wet-lab-validated AI for Protein. Use when the user asks for ME × Protein paper discovery, Top-journal-first screening, strict preprint exclusion, Chinese summaries, review quotas, QQ email delivery, DeepSeek cost control, GitHub Actions scheduling, deduplication history, or this radar repository.
---

# ME × Protein Paper Radar

Use this Skill to maintain the self-contained GitHub-ready pipeline in this directory. Read `README.md` for the user contract and `config/radar.json` for operational values before changing behavior.

## Safety contract

- Never write credentials, QQ SMTP authorization codes, API keys, or access tokens into repository files or Git remote URLs.
- Keep only secret names in `.env.example`; use environment variables or GitHub Secrets at runtime.
- Do not send a real email, call a paid model, or commit recommendation history unless the user explicitly asks for a live/test run.
- Prefer offline tests. A test email must use `mode=test`, include the `[TEST]` subject prefix, and never update recommendation history.
- A production history update is allowed only after successful SMTP delivery. Failed or incomplete runs must preserve recommendation history.
- Use only lawful public abstracts and open full text. Do not automate paywall, cookie, institutional-login, or access-control bypasses.

## Required behavior

1. Retrieve independently from configured public sources. Run topic queries plus source-appropriate Top-journal-targeted passes and a separate review-retrieval lane using native publication-type filters where available; parallelize by source while keeping requests within each source sequential. Treat a source failure as a recorded degradation, not proof that no papers exist.
2. Merge by normalized DOI; use title plus first-author similarity only as a fallback.
3. Apply cheap deterministic relevance filtering before model calls.
4. Verify Europe PMC open full text when available; otherwise use the public abstract. Exclude metadata-only records. Retry transient connection, incomplete-read, remote-disconnect, timeout, and HTTP transport failures with bounded backoff; never let one failed open-full-text fetch block abstract fallback.
5. Use DeepSeek `deepseek-v4-flash` in two stages: first run compact semantic/evidence screening for the candidate pool, then generate Chinese titles, reasons, and summaries only for the final 8–15 selected papers. Use non-thinking JSON mode, validate every response, and retry malformed or empty JSON. Isolate a single candidate after its retries are exhausted, record the failure, and abort only after the configured number of consecutive screening failures indicates a service outage.
6. Enforce microbial scope. Exclude plant/animal work; allow microbial communities; allow cell-free work only when directly supporting enzyme engineering or pathway validation.
7. Require original AI for Protein papers to contain a protein object, an AI method, a concrete protein task, and wet-lab validation. Reviews are exempt from the wet-lab rule. Downweight pure AI enzyme papers relative to metabolic engineering and integrated work.
8. Disable preprint discovery and delivery. Exclude source-, type-, DOI-, or model-identified preprints before selection and never write them to history.
9. Select 8–15 formal papers, including at least 2 Top-journal reviews and at least 8 configured Top-journal papers overall. Prefer up to 8 historical papers from the rolling six-year window, but treat the historical count as a soft preference: when fewer are qualified, backfill with recent papers and deliver normally. Deliver normally when only 8 or 9 papers satisfy every hard quality gate; do not wait for 10. Recent means publication within 30 days of the issue date. Do not relax the review quota or relabel an article as a review; if the dedicated review lane is exhausted, stop and alert. When the qualified pool permits, keep each canonical journal to at most 2 papers and each research track to at most 4; relax only these diversity limits when required to satisfy harder quality quotas, and record the relaxation.
10. Allow at most 2 non-Top original articles only when base_score is at least 94 and the supplied abstract/open-full-text evidence explicitly supports a configured exceptional novelty category. Label every exception and expose its evidence in the email. Routine optimization, unsupported first/novel wording, non-Top reviews, perspectives, and comments never qualify. Stop and alert when any quality gate is unmet.
11. Resolve journal aliases through `config/journals.json`. Treat conditional application journals as Top only when supplied evidence matches their configured scope terms, and treat review-only journals as Top only for reviews.
12. Generate Chinese summaries for all selected papers in static, no-JavaScript email HTML and expose targeted-retrieval, two-stage-model, candidate-pool, and diversity diagnostics in the footer.
13. Enforce the configured monthly CNY budget by reserving worst-case cost before each model attempt and settling with reported token usage. Stop and alert before exceeding the limit.
14. For public unattended deployments, keep credentials exclusively in GitHub Actions Secrets, document that forks receive no upstream secrets, and preserve monthly repository activity so GitHub does not disable scheduled workflows after prolonged inactivity.
15. Trigger a retry check every day at the configured non-round minute, but deliver at most once per Beijing-local ISO week. Checkout the latest default branch and check the committed delivery lock before discovery, model calls, and SMTP; recover a missing lock from same-week recommendation history, and exit with zero model cost when the week was already delivered. Mark the week sent only after successful SMTP delivery, commit a public last-check/last-success health record, and retry state pushes after rebasing on the latest default branch.

## Interactive literature support

When running inside Codex and the user requests manual supplement, verification, or deeper literature work, use the available `nature-academic-search` Skill. GitHub Actions cannot call a locally installed Codex Skill, so the cloud workflow must continue to use the independent adapters under `src/me_protein_radar/`.

## Validation

Before reporting completion:

```powershell
$env:PYTHONPATH = "src"
py -3.11 -m unittest discover -s tests -v
py -3.11 -m compileall -q src scripts tests
```

If Python 3.11 is unavailable locally, Python 3.9 is sufficient for the current offline test suite, while GitHub Actions remains pinned to Python 3.11. Also inspect `.github/workflows/weekly-radar.yml`, scan generated project files for hard-coded secrets, confirm test mode does not mutate `data/history.json` or `data/delivery_state.json`, and confirm a repeated production run exits before discovery and model construction.

## Main files

- `config/radar.json`: scope, quotas, source list, model, prices, and monthly budget.
- `config/journals.json`: canonical journal names, aliases, Top policies, conditional scope terms, and targeted-search flags.
- `src/me_protein_radar/discovery.py`: independent source adapters and cheap filtering.
- `src/me_protein_radar/verification.py`: open-full-text/abstract degradation policy.
- `src/me_protein_radar/deepseek.py`: structured model call and budget ledger.
- `src/me_protein_radar/selection.py`: scoring, quotas, deduplication, and history transaction.
- `src/me_protein_radar/delivery.py`: ISO-week delivery lock and public automation health state.
- `src/me_protein_radar/render.py`: static responsive HTML.
- `src/me_protein_radar/pipeline.py`: orchestration, delivery, and failure alert.
- `scripts/discovery_audit.py`: read-only retrieval diagnostic without DeepSeek, SMTP, or history mutation.
- `scripts/workflow_status.py`: public failure heartbeat when setup or offline validation fails before the main pipeline can record status.
- `.github/workflows/weekly-radar.yml`: daily 10:46 Beijing retry check with one successful delivery per ISO week.
