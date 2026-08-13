from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

from .io_utils import normalize_doi


TRACKS = {"integrated": "ME × Protein 协同", "metabolic_engineering": "代谢工程", "enzyme_engineering": "酶工程 / 生物催化", "ai_protein": "AI for Protein"}
VERIFY = {"full_text": "开放全文核验", "abstract": "公开摘要核验"}


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def link(record: dict[str, Any]) -> str:
    doi = normalize_doi(record.get("doi"))
    return "https://doi.org/" + quote(doi, safe="/:()[];._-") if doi else str(record.get("url", ""))


def block(record: dict[str, Any], anchor: str, number: str, *, preprint: bool = False) -> str:
    if preprint: badge = "前沿预警 · 未经同行评议"
    elif record.get("document_type") == "review": badge = "科研视角 · 综述"
    else: badge = TRACKS.get(str(record.get("track")), "原创研究")
    methods = "、".join(map(str, record.get("methods") or [])) or "未记录"
    sources = " / ".join(map(str, record.get("source_labels") or [])) or "未记录"
    action = "查看预印本" if preprint and not record.get("doi") else "打开 DOI"
    uncertainty = f'<br><strong>证据边界：</strong>{esc(record.get("uncertainty_note"))}' if record.get("uncertainty_note") else ""
    return f'''<table id="{esc(anchor)}" role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;border:1px solid #dce3ea;border-radius:8px;background:#fff">
<tr><td style="padding:20px 20px 8px"><div style="font-size:12px;color:#0b6b62;font-weight:bold">{esc(badge)} · {float(record.get('final_score', 0)):.1f} 分 · {esc(VERIFY.get(record.get('verification_level'), '核验未知'))}</div><h2 style="margin:8px 0 6px;font-size:20px;line-height:1.4;color:#17212b">{esc(number)}. {esc(record.get('title_zh') or record.get('title'))}</h2><div style="font-size:13px;line-height:1.55;color:#66717d">{esc(record.get('title'))}</div></td></tr>
<tr><td style="padding:10px 20px"><div style="font-size:13px;color:#0b6b62;font-weight:bold;margin-bottom:5px">推荐理由</div><div style="font-size:16px;line-height:1.75;color:#26323d">{esc(record.get('recommendation_reason_zh'))}</div></td></tr>
<tr><td style="padding:10px 20px"><div style="font-size:13px;color:#0b6b62;font-weight:bold;margin-bottom:5px">中文速读摘要</div><div style="font-size:16px;line-height:1.75;color:#26323d">{esc(record.get('summary_zh'))}</div></td></tr>
<tr><td style="padding:10px 20px 4px;font-size:14px;line-height:1.7;color:#56616c"><strong>期刊/平台：</strong>{esc(record.get('journal') or '预印本')}<br><strong>日期：</strong>{esc(record.get('publication_date'))}　<strong>对象：</strong>{esc(record.get('host_or_object') or '未记录')}<br><strong>产物/任务：</strong>{esc(record.get('product_or_task') or '未记录')}<br><strong>方法：</strong>{esc(methods)}<br><strong>来源：</strong>{esc(sources)}<br><strong>核验：</strong>{esc(record.get('verification_note'))}{uncertainty}</td></tr>
<tr><td style="padding:14px 20px 22px"><a href="{esc(link(record))}" style="display:inline-block;background:#0b6b62;color:#fff;text-decoration:none;font-size:15px;font-weight:bold;padding:11px 18px;border-radius:5px">{esc(action)}</a><a href="#top" style="display:inline-block;margin-left:14px;color:#52606d;font-size:13px;text-decoration:underline">返回目录</a></td></tr></table>'''


def render(selection: dict[str, Any], warnings: list[str] | None = None) -> str:
    research = selection["selected_research"]
    reviews = selection["selected_reviews"]
    preprints = selection["preprint_watchlist"]
    formal = selection["selected_formal"]
    top_count = sum(bool(x.get("top_journal")) for x in formal)
    formal_zh = sum(bool(x.get("summary_zh")) for x in formal)
    preprint_zh = sum(bool(x.get("summary_zh")) for x in preprints)
    toc = ['<div style="margin:12px 0 4px;font-weight:bold">原创研究</div>']
    toc += [f'<div style="margin:7px 0"><a href="#paper-{i}" style="color:#0b6b62">{i}. {esc(x.get("title_zh") or x.get("title"))}</a></div>' for i, x in enumerate(research, 1)]
    toc += ['<div style="margin:14px 0 4px;font-weight:bold;color:#6b4f00">科研视角 · 综述</div>']
    toc += [f'<div style="margin:7px 0"><a href="#review-{i}" style="color:#8a5a00">R{i}. {esc(x.get("title_zh") or x.get("title"))}</a></div>' for i, x in enumerate(reviews, 1)]
    if preprints:
        toc += ['<div style="margin:14px 0 4px;font-weight:bold;color:#8a5a00">前沿预警</div>']
        toc += [f'<div style="margin:7px 0"><a href="#preprint-{i}" style="color:#8a5a00">P{i}. {esc(x.get("title_zh") or x.get("title"))}</a></div>' for i, x in enumerate(preprints, 1)]
    blocks = "".join(block(x, f"paper-{i}", str(i)) for i, x in enumerate(research, 1))
    blocks += '<table role="presentation" width="100%" style="margin:26px 0 14px;background:#fff8e8;border-left:5px solid #b17a00"><tr><td style="padding:16px 18px"><h2 style="margin:0 0 5px;color:#6b4f00">科研视角 · Reviews</h2><div style="font-size:14px;color:#6b4f00">每期至少 2 篇高质量综述，用于补充领域框架、趋势与方法学视角。</div></td></tr></table>'
    blocks += "".join(block(x, f"review-{i}", f"R{i}") for i, x in enumerate(reviews, 1))
    if preprints:
        blocks += '<table role="presentation" width="100%" style="margin:26px 0 14px;background:#fff7e6;border-left:5px solid #d99100"><tr><td style="padding:16px 18px"><h2 style="margin:0;color:#714600">前沿预警</h2><div style="font-size:14px;color:#714600">以下工作尚未完成同行评议，与正式论文分开呈现。</div></td></tr></table>'
        blocks += "".join(block(x, f"preprint-{i}", f"P{i}", preprint=True) for i, x in enumerate(preprints, 1))
    warning_text = "；".join((warnings or [])[:5])
    warning_html = f'<div style="margin-top:12px;color:#8a5a00">部分来源降级：{esc(warning_text)}</div>' if warning_text else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ME × Protein 周报</title></head><body style="margin:0;background:#eef2f5;font-family:Arial,'Microsoft YaHei',sans-serif;color:#26323d"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:18px 8px"><table id="top" role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;background:#f8fafb">
<tr><td style="padding:28px 22px;background:#103c3a;color:#fff"><div style="font-size:14px;letter-spacing:1.6px;color:#bde7df">WEEKLY LITERATURE RADAR</div><h1 style="margin:8px 0 4px;font-size:32px">ME × Protein</h1><div style="font-size:15px;color:#d9efeb">{esc(selection['issue_date'])} · 微生物代谢工程与 AI 酶工程精选</div></td></tr>
<tr><td style="padding:16px 14px;background:#fff"><table role="presentation" width="100%" cellspacing="6"><tr><td align="center" style="padding:12px;background:#e9f5f2"><div style="font-size:25px;font-weight:bold;color:#0b6b62">{len(formal)}</div><div style="font-size:12px">精选论文</div></td><td align="center" style="padding:12px;background:#fff4d6"><div style="font-size:25px;font-weight:bold;color:#8a5a00">{len(reviews)}</div><div style="font-size:12px">高质量综述</div></td></tr><tr><td align="center" style="padding:12px;background:#e9f5f2"><div style="font-size:25px;font-weight:bold;color:#0b6b62">{top_count}</div><div style="font-size:12px">Top 期刊</div></td><td align="center" style="padding:12px;background:#e9f5f2"><div style="font-size:25px;font-weight:bold;color:#0b6b62">{formal_zh} + {preprint_zh}</div><div style="font-size:12px">中文摘要（正式+预印本）</div></td></tr></table></td></tr>
<tr><td style="padding:18px 22px;background:#fff;border-top:1px solid #e2e8ed"><h2 style="margin:0 0 10px">目录</h2>{''.join(toc)}</td></tr><tr><td style="padding:24px 14px 4px">{blocks}</td></tr>
<tr><td style="padding:20px 22px 28px;font-size:12px;line-height:1.6;color:#6b7681">摘要级核验仅复述公开摘要明确支持的结论；开放全文仅使用合法公开来源；预印本未经同行评议。{warning_html}</td></tr></table></td></tr></table></body></html>'''


def subject(mode: str, issue_date: str, count: int) -> str:
    return ("[TEST] " if mode == "test" else "") + f"ME × Protein 周报 | {issue_date} | 精选 {count} 篇"

