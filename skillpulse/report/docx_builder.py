"""Word (.docx) report: title page, a heading per role family, real Word tables, coloured ↑/↓ arrows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from skillpulse.report.markdown import fmt_trend

GREEN, RED, GREY = RGBColor(0x1B, 0x7F, 0x3B), RGBColor(0xB4, 0x23, 0x18), RGBColor(0x66, 0x66, 0x66)
TABLE_STYLE = "Light Grid Accent 1"


def _hyperlink(paragraph, url: str, text: str) -> None:
    """python-docx has no public hyperlink API; build the w:hyperlink element directly."""
    r_id = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.append(color)
    rpr.append(u)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _table(doc, headers: list[str], rows: list[list[Any]], arrow_cols: tuple[int, ...] = ()) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = TABLE_STYLE
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(h)
        run.bold = True
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            p = cells[i].paragraphs[0]
            text = str(v)
            run = p.add_run(text)
            run.font.size = Pt(9)
            if i in arrow_cols:
                run.font.color.rgb = GREEN if text.startswith("↑") else RED if text.startswith("↓") else GREY
    doc.add_paragraph()


def _skills(doc, skills: list[dict[str, Any]]) -> None:
    _table(doc, ["#", "Skill", "Share", "vs 7d", "vs 30d"],
           [[i + 1, s["skill"], f"{s['share']:.0f}%", fmt_trend(s["arrow7"], s["d7"]),
             fmt_trend(s["arrow30"], s["d30"])] for i, s in enumerate(skills)],
           arrow_cols=(3, 4))


def _focus(doc, ft: list[dict[str, Any]]) -> None:
    rows = [[f["group"], f["tech"], f"{f['must']:.0f}%", f"{f['nice']:.0f}%", f"{f['any']:.0f}%"]
            for f in ft if f["any"] or f["must"] or f["nice"]]
    if rows:
        doc.add_paragraph().add_run("Focus technologies").bold = True
        _table(doc, ["Group", "Technology", "Must-have", "Nice-to-have", "Any mention"], rows)


def build_docx(d: dict[str, Any], path: Path) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    g = d["glance"]
    s = g["split"]

    # --- Title page
    for _ in range(6):
        doc.add_paragraph()
    title = doc.add_paragraph("SkillPulse", style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph(f"Daily job-skills trends · {s['preferred_city']} first · India remote & hybrid", style="Subtitle")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(d["date"])
    r.bold = True
    r.font.size = Pt(16)
    p = doc.add_paragraph(f"{g['total_postings']} postings in the {g['ttl_days']}-day snapshot · {g['new_today']} new today")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # --- 1. Glance
    doc.add_heading("1. Today at a glance", level=1)
    doc.add_paragraph(g.get("paragraph", ""))
    _table(doc, ["Metric", "Value"], [
        ["Postings in snapshot", g["total_postings"]], ["New today", g["new_today"]],
        ["Seen again (cached)", g["cached_today"]], ["Analysed (extracted)", g["analysed"]],
        [f"{s['preferred_city']} (hybrid / remote)", f"{s['preferred_total']} ({s['preferred_hybrid']} / {s['preferred_remote']})"],
        ["India-remote (outside preferred city)", s["india_remote"]], ["Hybrid elsewhere in India", s["hybrid_elsewhere"]],
        ["By level", ", ".join(f"{k} {v}" for k, v in g["levels"].items() if v) or "–"],
    ])
    doc.add_paragraph().add_run("Top rising skills (L1–L2, vs 7-day baseline)").bold = True
    if g["top_rising"]:
        _table(doc, ["Skill", "Share", "Δ 7d"],
               [[x["skill"] + (" (new)" if x.get("new") else ""), f"{x['share']:.0f}%", f"↑ +{x['d7']}"] for x in g["top_rising"]],
               arrow_cols=(2,))
    else:
        doc.add_paragraph("No baseline yet. Trends appear once a few daily runs are stored."
                          if not g["baseline_available"] else "No skill rose by more than the threshold.")

    # --- 2. Families
    doc.add_heading("2. By role family", level=1)
    for fam in d["families"]:
        doc.add_heading(fam["label"], level=2)
        if not fam["levels"]:
            doc.add_paragraph("No analysed postings in this family yet.")
        for lv in fam["levels"]:
            doc.add_heading(f"{lv['level']}{'' if lv['primary'] else ' (secondary)'} — {lv['n']} postings", level=3)
            doc.add_paragraph().add_run(f"Top {len(lv['top_skills'])} skills (% of postings, Δ in percentage points)").bold = True
            _skills(doc, lv["top_skills"])
            if lv["primary"] and lv.get("rising"):
                doc.add_paragraph("New / rising: " + ", ".join(
                    f"{x['skill']} (+{x['d7']}pp{', new' if x['new'] else ''})" for x in lv["rising"]))
            if lv["certifications"]:
                doc.add_paragraph().add_run("Certifications in demand").bold = True
                _table(doc, ["Certification", "Postings", "Share"],
                       [[c["name"], c["count"], f"{c['share']:.0f}%"] for c in lv["certifications"]])
            _focus(doc, lv["focus_table"])
            if lv["primary"]:
                p = doc.add_paragraph()
                p.add_run("Ideal candidate: ").bold = True
                p.add_run(lv.get("ideal_candidate", ""))
                if lv.get("samples"):
                    doc.add_paragraph().add_run("Sample postings").bold = True
                    for smp in lv["samples"]:
                        p = doc.add_paragraph(style="List Bullet")
                        _hyperlink(p, smp["url"], f"{smp['title']} — {smp['company']}")
                        p.add_run(f" · {smp['city'] or 'India'} · {smp['work_mode']}")

    # --- 3. Certifications
    doc.add_heading("3. Certifications across the market", level=1)
    lvls = d["config"]["report_levels"]
    if d["certifications_market"]:
        _table(doc, ["Certification", "Total", *lvls],
               [[c["name"], c["total"], *[c["by_level"].get(x, 0) for x in lvls]] for c in d["certifications_market"]])
    else:
        doc.add_paragraph("No certifications mentioned yet.")

    # --- 4. Health
    h = d["health"]
    doc.add_heading("4. Sources and run health", level=1)
    _table(doc, ["Source", "Status", "Fetched", "Note"],
           [[x["name"], "ok" if x["ok"] and not x.get("skipped_reason") else ("skipped" if x["ok"] else "FAILED"),
             x.get("fetched", 0), x.get("error") or x.get("skipped_reason") or ""] for x in h.get("sources", [])])
    _table(doc, ["Metric", "Value"], [
        ["Cache hit rate", f"{h.get('cache_hit_rate', 0):.0f}%"],
        ["LLM provider", h.get("provider", "–")], ["LLM calls", h.get("llm_calls", 0)],
        ["Tokens in / out", f"{h.get('input_tokens', 0):,} / {h.get('output_tokens', 0):,}"],
        ["Cost (budget)", f"${h.get('cost_usd', 0):.4f} (${h.get('budget_usd', 0):.2f})"],
        ["Extracted / reused / deferred / failed",
         f"{h.get('extracted', 0)} / {h.get('extraction_cache_hits', 0)} / {h.get('skipped_budget', 0)} / {h.get('extract_failed', 0)}"],
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path
