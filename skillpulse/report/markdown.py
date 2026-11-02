"""Render report data (dict from aggregate.build_report_data + prose) to Markdown."""

from __future__ import annotations

from typing import Any


def fmt_delta(d: float | None) -> str:
    if d is None:
        return "–"
    return f"{d:+.1f}"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    return out


def skills_rows(skills: list[dict[str, Any]]) -> list[list[Any]]:
    return [[i + 1, s["skill"], f"{s['share']:.0f}%", f"{s['arrow7']} {fmt_delta(s['d7'])}",
             f"{s['arrow30']} {fmt_delta(s['d30'])}"] for i, s in enumerate(skills)]


def focus_rows(ft: list[dict[str, Any]]) -> list[list[Any]]:
    return [[f["group"], f["tech"], f"{f['must']:.0f}%", f"{f['nice']:.0f}%", f"{f['any']:.0f}%"]
            for f in ft if f["any"] or f["must"] or f["nice"]]


def split_line(g: dict[str, Any]) -> str:
    s = g["split"]
    return (f"{s['preferred_city']}: **{s['preferred_total']}** (hybrid {s['preferred_hybrid']}, remote {s['preferred_remote']}) · "
            f"India-remote: **{s['india_remote']}** · Hybrid elsewhere in India: **{s['hybrid_elsewhere']}**")


def render_markdown(d: dict[str, Any]) -> str:
    g = d["glance"]
    L: list[str] = [f"# SkillPulse — {d['date']}", ""]
    L += ["## 1. Today at a glance", "", g.get("paragraph", ""), ""]
    L += [f"- Postings in snapshot ({g['ttl_days']}-day window): **{g['total_postings']}** — "
          f"new today **{g['new_today']}**, seen again (cached) **{g['cached_today']}**, analysed **{g['analysed']}**",
          f"- {split_line(g)}",
          "- By level: " + ", ".join(f"{k} {v}" for k, v in g["levels"].items() if v), ""]
    L.append("**Top rising skills (L1–L2, vs 7-day baseline)**")
    if g["top_rising"]:
        L += [""] + _table(["Skill", "Share", "Δ 7d (pp)"],
                           [[r["skill"] + (" 🆕" if r.get("new") else ""), f"{r['share']:.0f}%", f"+{r['d7']}"]
                            for r in g["top_rising"]])
    else:
        L.append("" if g["baseline_available"] else "_No baseline yet. Trends appear once a few daily runs are stored._")
        if g["baseline_available"]:
            L.append("_No skill rose by more than the threshold._")
    L.append("")

    L += ["## 2. By role family", ""]
    for fam in d["families"]:
        L += [f"### {fam['label']}", ""]
        if not fam["levels"]:
            L += ["_No analysed postings in this family yet._", ""]
        for lv in fam["levels"]:
            tag = "" if lv["primary"] else " (secondary)"
            L += [f"#### {lv['level']}{tag} — {lv['n']} postings", ""]
            L += [f"**Top {len(lv['top_skills'])} skills** (% of postings; Δ vs 7-day / 30-day baseline, pp)", ""]
            L += _table(["#", "Skill", "Share", "7d", "30d"], skills_rows(lv["top_skills"])) + [""]
            if lv["primary"]:
                if lv.get("rising"):
                    L += ["**New / rising:** " + ", ".join(
                        f"{r['skill']} (+{r['d7']}pp{', new' if r['new'] else ''})" for r in lv["rising"]), ""]
            if lv["certifications"]:
                L += ["**Certifications in demand:** " + "; ".join(
                    f"{c['name']} ({c['share']:.0f}%)" for c in lv["certifications"]), ""]
            fr = focus_rows(lv["focus_table"])
            if fr:
                L += ["**Focus technologies**", ""] + _table(["Group", "Tech", "Must-have", "Nice-to-have", "Any mention"], fr) + [""]
            if lv["primary"]:
                L += ["**Ideal candidate:** " + lv.get("ideal_candidate", ""), ""]
                if lv.get("samples"):
                    L.append("**Sample postings:**")
                    L += [f"- [{s['title']} — {s['company']}]({s['url']}) · {s['city'] or 'India'} · {s['work_mode']}"
                          for s in lv["samples"]]
                    L.append("")

    L += ["## 3. Certifications across the market", ""]
    if d["certifications_market"]:
        lvls = d["config"]["report_levels"]
        L += _table(["Certification", "Total", *lvls],
                    [[c["name"], c["total"], *[c["by_level"].get(x, 0) for x in lvls]] for c in d["certifications_market"]])
    else:
        L.append("_No certifications mentioned yet._")
    L.append("")

    h = d["health"]
    L += ["## 4. Sources and run health", ""]
    L += _table(["Source", "Status", "Fetched", "Note"],
                [[s["name"], "ok" if s["ok"] and not s.get("skipped_reason") else ("skipped" if s["ok"] else "FAILED"),
                  s.get("fetched", 0), s.get("error") or s.get("skipped_reason") or ""] for s in h.get("sources", [])])
    L += ["",
          f"- Cache hit rate: {h.get('cache_hit_rate', 0):.0f}% (postings already known / fetched)",
          f"- LLM: {h.get('provider', '–')} · {h.get('llm_calls', 0)} calls · {h.get('input_tokens', 0):,} in / "
          f"{h.get('output_tokens', 0):,} out tokens · ${h.get('cost_usd', 0):.4f} of ${h.get('budget_usd', 0):.2f} budget",
          f"- Extracted this run: {h.get('extracted', 0)} · reused from cache: {h.get('extraction_cache_hits', 0)} · "
          f"deferred by cost guard: {h.get('skipped_budget', 0)} · failed: {h.get('extract_failed', 0)}",
          f"- Prose: {d.get('prose_source', 'deterministic')}", ""]
    return "\n".join(L)
