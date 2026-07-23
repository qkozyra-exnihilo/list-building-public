#!/usr/bin/env python3
"""
qualification_artifact.py — Qualification-funnel HTML report (post Step 2b)
============================================================================
Run AFTER the company-qualification half of the pipeline (Steps 1 → 2b),
before the manual Pronto search. Emits one self-contained HTML page:

  {project_name}/{project_name}_qualification_report.html

The page shows the full funnel (seed → cleaning → ICP → Apollo/size →
sales-motion → Pronto-ready), every dropped company with its stage and
reason, and the final Pronto-ready list with ICP confidence, sales-team
size and pricing-page status. Publish it as a Claude artifact so the run
is reviewable at a glance (standing request 2026-07-02).

Design tokens follow the "ICP + sales motion" artifact family (ground
#f6f7f9, ink #161a22, indigo accent, green/crimson pills) so all
qualification reports read as one system.

Usage:
    python3 qualification_artifact.py <project_name> [--title "Nice Title"]
"""

import argparse
import csv
import html
import os
import sys
from datetime import date


def norm_domain(d):
    d = (d or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if d.startswith(p):
            d = d[len(p):]
    return d.rstrip("/").split("/")[0]


def read_csv(path):
    if not os.path.isfile(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_true(v):
    return (v or "").strip().lower() in ("true", "1", "yes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_name")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    pdir = os.path.join(repo_root, args.project_name)
    p = args.project_name
    f = lambda suffix: os.path.join(pdir, f"{p}_{suffix}")

    step1 = read_csv(f("step1_companies.csv"))
    step1b = read_csv(f("step1b_companies_cleaned.csv"))
    step1d = read_csv(f("step1d_icp_filtered.csv"))
    classif = read_csv(f("step1d_classification.csv")) or []
    step1f = read_csv(f("step1f_pricing_validated.csv"))
    step2 = read_csv(f("step2_companies_with_linkedin.csv"))
    step2a = read_csv(f("step2a_companies_sales_motion.csv"))
    step2a_rep = read_csv(f("step2a_sales_motion_report.csv")) or []
    pronto = read_csv(f("step2b_pronto_import.csv"))
    db_contacts = read_csv(f("step2b_db_contacts.csv")) or []
    # Post-Step-4 files (present once the Pronto export has been refined) —
    # when they exist, the report gains a Coverage section automatically.
    step4 = read_csv(f("step4_contacts_filtered.csv"))
    uncovered = read_csv(f("uncovered_companies.csv"))

    if step1 is None or pronto is None:
        print("ERROR: need at least step1_companies.csv and step2b_pronto_import.csv")
        sys.exit(1)

    apollo = read_csv(os.path.join(repo_root, "database", "apollo_companies_database.csv")) or []
    apollo_by_dom = {norm_domain(r.get("domain", "")): r for r in apollo}

    classif_by_dom = {norm_domain(r.get("domain", "")): r for r in classif}
    rep_by_dom = {norm_domain(r.get("domain", "")): r for r in step2a_rep}
    pricing_by_dom = {norm_domain(r.get("domain", "")): r for r in (step1f or [])}
    pronto_by_dom = {norm_domain(r.get("Company Website", "")): r for r in pronto}

    # ── Funnel stages (skip missing files gracefully) ─────────────────────────
    stages = []
    def add_stage(label, sub, rows):
        if rows is not None:
            stages.append({"label": label, "sub": sub,
                           "doms": {norm_domain(r.get("domain") or r.get("Company Website") or "") for r in rows},
                           "n": len(rows)})
    add_stage("Seed list", "Step 1 — standardized input", step1)
    add_stage("Cleaning", "Step 1b — dedup, domain fixes", step1b)
    add_stage("ICP gate", "Step 1d — recurring/usage revenue model (LLM, threshold 0.80)", step1d)
    add_stage("Apollo + size gate", "Step 2 — NAICS safety net + ≤500 employees", step2)
    add_stage("Sales-motion gate", "Step 2a — sales+BD headcount ≥ 2 (Apollo)", step2a)
    add_stage("Pronto-ready", "Step 2b — needs fresh contact search", pronto)

    # ── Dropped companies with stage + reason ─────────────────────────────────
    name_by_dom = {}
    for r in (step1b or step1):
        name_by_dom[norm_domain(r.get("domain", ""))] = r.get("company_name", "")

    dropped = []
    for i in range(1, len(stages)):
        prev, cur = stages[i - 1], stages[i]
        for dom in sorted(prev["doms"] - cur["doms"]):
            if not dom:
                continue
            name = name_by_dom.get(dom, dom)
            stage_lbl = cur["label"]
            reason, detail = "", ""
            if "ICP" in stage_lbl:
                c = classif_by_dom.get(dom, {})
                reason = f"Not ICP ({c.get('classification','?')} @ {c.get('confidence','?')})"
                detail = c.get("reason", "")
            elif "Apollo" in stage_lbl:
                a = apollo_by_dom.get(dom, {})
                emp = (a.get("estimated_num_employees") or "").strip()
                if emp and emp.replace(".", "").isdigit() and float(emp) > 500:
                    reason = "Size gate (>500 employees)"
                    detail = f"{int(float(emp)):,} employees (Apollo)"
                else:
                    reason = "NAICS / keyword filter"
                    detail = a.get("industry", "")
            elif "Sales-motion" in stage_lbl:
                rrow = rep_by_dom.get(dom, {})
                reason = "No sales motion"
                detail = (f"sales={rrow.get('sales_headcount','?')} "
                          f"bd={rrow.get('bd_headcount','?')} — {rrow.get('sales_motion_reason','')}")
            elif "Cleaning" in stage_lbl:
                reason = "Cleaning"
                detail = "duplicate domain or missing domain"
            elif "Pronto-ready" in stage_lbl:
                # NOT a drop: the company is qualified, its contacts just come
                # from the DB cache instead of a fresh (paid) search.
                continue
            dropped.append({"name": name, "dom": dom, "stage": stage_lbl,
                            "reason": reason, "detail": detail})

    # ── Qualified rows (ALL step2a survivors; DB-cache-covered included) ───────
    n_db_covered = 0
    qualified = []
    for r in (step2a or step2 or []):
        dom = norm_domain(r.get("domain", ""))
        c = classif_by_dom.get(dom, {})
        pr = pricing_by_dom.get(dom, {})
        in_import = dom in pronto_by_dom
        if not in_import:
            n_db_covered += 1
        qualified.append({
            "name": r.get("company_name", ""), "dom": dom,
            "li": (pronto_by_dom[dom].get("LinkedIn ID", "") if in_import else "DB cache"),
            "conf": c.get("confidence", ""), "vertical": c.get("vertical", ""),
            "sales": r.get("sales_headcount", ""), "bd": r.get("bd_headcount", ""),
            "emp": r.get("employee_count", ""),
            "pricing": is_true(pr.get("valid_pricing_page", "")),
            "purl": pr.get("pricing_page_url", ""),
        })
    qualified.sort(key=lambda x: -(int(x["sales"] or 0) + int(x["bd"] or 0)))

    # Qualified = step2a survivors; the 2b stage only splits fresh-search vs DB cache.
    stages[-1]["sub"] += (f" ({n_db_covered} more covered by the contact-DB cache)"
                          if n_db_covered else "")
    n_seed, n_final = stages[0]["n"], len(qualified)
    n_dropped = len(dropped)
    n_pricing = sum(1 for q in qualified if q["pricing"])
    title = args.title or p.replace("_", " ")
    today = date.today().isoformat()

    e = html.escape

    # ── Funnel rows ────────────────────────────────────────────────────────────
    funnel_html = []
    for i, s in enumerate(stages):
        pct = 100.0 * s["n"] / max(n_seed, 1)
        drop_n = (stages[i - 1]["n"] - s["n"]) if i else 0
        if s["label"] == "Pronto-ready" and drop_n:
            # DB-cache-covered companies are not drops
            drop_html = '<span class="fdrop none">·</span>'
        else:
            drop_html = f'<span class="fdrop">−{drop_n}</span>' if drop_n else '<span class="fdrop none">·</span>'
        funnel_html.append(
            f'<div class="frow"><div class="flab"><b>{e(s["label"])}</b>'
            f'<span>{e(s["sub"])}</span></div>'
            f'<div class="fbarwrap"><div class="fbar" style="width:{pct:.1f}%">'
            f'<span class="fn">{s["n"]}</span></div></div>{drop_html}</div>')

    # ── Dropped table rows ─────────────────────────────────────────────────────
    drop_rows = []
    for d in dropped:
        cls = ("drop" if "ICP" in d["reason"] or "sales" in d["reason"].lower() or "No sales" in d["reason"]
               else "unc")
        drop_rows.append(
            f'<tr><td><span class="co">{e(d["name"])}</span>'
            f'<a class="dom" href="https://{e(d["dom"])}" target="_blank" rel="noopener">{e(d["dom"])}</a></td>'
            f'<td>{e(d["stage"])}</td>'
            f'<td><span class="pill {cls}">{e(d["reason"])}</span></td>'
            f'<td class="detail">{e(d["detail"])}</td></tr>')

    # ── Qualified table rows ───────────────────────────────────────────────────
    q_rows = []
    for q in qualified:
        motion = (int(q["sales"] or 0) + int(q["bd"] or 0))
        pricing_cell = (f'<a class="pok" href="{e(q["purl"])}" target="_blank" rel="noopener">✓ page</a>'
                        if q["pricing"] else '<span class="pnone">—</span>')
        q_rows.append(
            f'<tr><td><span class="co">{e(q["name"])}</span>'
            f'<a class="dom" href="https://{e(q["dom"])}" target="_blank" rel="noopener">{e(q["dom"])}</a></td>'
            f'<td class="num"><span class="sh">{motion}</span> <span class="emp">({e(str(q["sales"]))}s+{e(str(q["bd"]))}bd)</span></td>'
            f'<td class="num">{e(str(q["emp"] or "?"))}</td>'
            f'<td class="num">{e(str(q["conf"]))}</td>'
            f'<td class="vertcell">{e(q["vertical"])}</td>'
            f'<td>{pricing_cell}</td>'
            f'<td class="licell">{e(q["li"])}</td></tr>')

    # ── Coverage section (post Step 4, standing request 2026-07-02) ───────────
    coverage_html = ""
    if step4 is not None and uncovered is not None:
        n_companies = n_final
        unc_zero = [u for u in uncovered if u.get("status") == "zero_pronto_leads"]
        unc_filt = [u for u in uncovered if u.get("status") == "leads_all_filtered"]
        # Covered = qualified companies with >=1 kept contact in step4 (match by
        # domain or company-LinkedIn slug — some export rows carry no domain).
        slug_to_dom = {}
        for r in (step2a or []):
            u = (r.get("linkedin_company_url") or "").lower().rstrip("/")
            if "/company/" in u:
                slug_to_dom[u.split("/company/")[-1]] = norm_domain(r.get("domain", ""))
        qualified_doms = {q["dom"] for q in qualified}
        covered_doms = set()
        for r in step4:
            d = norm_domain(r.get("Company Domain") or r.get("Company Website") or "")
            if d not in qualified_doms:
                u = (r.get("Company Linkedin Flagship Url") or "").lower().rstrip("/")
                slug = u.split("/company/")[-1] if "/company/" in u else ""
                d = slug_to_dom.get(slug, d)
            if d in qualified_doms:
                covered_doms.add(d)
        n_cov = len(covered_doms)
        from collections import Counter
        personas = Counter((r.get("_priority_category") or "?") for r in step4)
        persona_str = " · ".join(f"{k} {v}" for k, v in personas.most_common())

        unc_rows = []
        for u in sorted(uncovered, key=lambda x: (x.get("status") != "zero_pronto_leads",
                                                  -int(x.get("pronto_leads") or 0))):
            zero = u.get("status") == "zero_pronto_leads"
            pill = ('<span class="pill drop">0 Pronto leads</span>' if zero
                    else '<span class="pill unc">No persona match</span>')
            detail = ("search returned nothing — verify slug / company too small"
                      if zero else
                      f"{u.get('pronto_leads','?')} lead(s) exported, none passed the Finance/Revenue/Ops filter")
            unc_rows.append(
                f'<tr><td><span class="co">{e(u.get("company_name",""))}</span>'
                f'<a class="dom" href="https://{e(u.get("domain",""))}" target="_blank" rel="noopener">{e(u.get("domain",""))}</a></td>'
                f'<td>{pill}</td><td class="detail">{detail}</td></tr>')

        coverage_html = f"""
<h2>Contact coverage — after Pronto search (Step 4)</h2>
<p class="h2sub">{len(step4)} contacts kept ({e(persona_str)}). Uncovered companies split by cause.</p>
<section class="stats">
<div class="stat q"><span class="num">{n_cov}/{n_companies}</span><span class="lab">Companies covered (≥1 contact)</span></div>
<div class="stat d"><span class="num">{len(unc_zero)}</span><span class="lab">Uncovered — 0 leads in Pronto export</span></div>
<div class="stat pr" style="border-top-color:var(--unc)"><span class="num" style="color:var(--unc)">{len(unc_filt)}</span><span class="lab">Uncovered — leads exported, no persona match</span></div>
<div class="stat seed"><span class="num">{len(step4)}</span><span class="lab">Contacts kept for enrichment</span></div>
</section>
<div class="tablewrap" style="margin-top:14px"><table><thead><tr><th>Company</th><th>Cause</th><th>Detail</th></tr></thead>
<tbody>{''.join(unc_rows)}</tbody></table></div>
<p class="note">"0 Pronto leads" = the account-list search returned nothing (check the LinkedIn slug, or the company is too small). "No persona match" = leads existed but none survived the Finance &gt; Revenue &gt; Ops filter — iteration candidates for a broader search.</p>
"""

    db_note = ""
    if db_contacts:
        db_note = f'<p class="note">{len(db_contacts)} contacts pulled from the DB cache (companies covered without a new search).</p>'
    else:
        db_note = '<p class="note">0 companies covered by the contact-DB cache — all need a fresh Pronto search.</p>'

    page = f"""<title>List qualification — {e(title)}</title>
<style>
*{{box-sizing:border-box}}
:root{{--ground:#f6f7f9;--panel:#fff;--ink:#161a22;--soft:#5b6472;--faint:#8b94a3;--line:#e4e7ec;--line2:#eef0f3;--accent:#4f46e5;--pass:#15803d;--pass-bg:#e9f6ed;--unc:#b4540a;--unc-bg:#fcf1e2;--drop:#be123c;--drop-bg:#fcebef}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1140px;margin:0 auto;padding:0 28px 80px}}
.masthead{{margin:0 -28px 26px;padding:0 28px;background:linear-gradient(180deg,#1c2030,#161a26);color:#eef0f5}}
.mast-in{{max-width:1084px;margin:0 auto;padding:38px 0 34px}}
.eyebrow{{font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;color:#9aa4bd;font-weight:600}}
h1{{font-size:31px;line-height:1.08;letter-spacing:-.02em;font-weight:760;margin:12px 0 10px;text-wrap:balance}}
.lede{{max-width:78ch;color:#c3cad9;font-size:14.5px;margin:0}}
h2{{font-size:17px;font-weight:680;letter-spacing:-.01em;margin:40px 0 4px}}
.h2sub{{font-size:13px;color:var(--faint);margin:0 0 14px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}}
.stat{{border:1px solid var(--line);border-top-width:3px;background:var(--panel);border-radius:12px;padding:15px 17px;display:flex;flex-direction:column;gap:2px}}
.stat .num{{font-size:31px;font-weight:760;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1}}
.stat .lab{{font-size:12.5px;color:var(--soft)}}
.stat.seed{{border-top-color:var(--faint)}}.stat.q{{border-top-color:var(--accent)}}.stat.q .num{{color:var(--accent)}}
.stat.d{{border-top-color:var(--drop)}}.stat.d .num{{color:var(--drop)}}
.stat.pr{{border-top-color:var(--pass)}}.stat.pr .num{{color:var(--pass)}}
.funnel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px 22px;display:flex;flex-direction:column;gap:13px}}
.frow{{display:grid;grid-template-columns:250px 1fr 52px;gap:16px;align-items:center}}
.flab b{{display:block;font-size:13.5px;font-weight:640}}
.flab span{{display:block;font-size:11.5px;color:var(--faint);line-height:1.35}}
.fbarwrap{{background:var(--line2);border-radius:6px;height:26px;overflow:hidden}}
.fbar{{background:var(--accent);height:100%;border-radius:6px 4px 4px 6px;display:flex;align-items:center;justify-content:flex-end;min-width:34px;transition:width .4s}}
.fn{{color:#fff;font-size:12.5px;font-weight:680;padding:0 9px;font-variant-numeric:tabular-nums}}
.fdrop{{font-size:13px;font-weight:660;color:var(--drop);font-variant-numeric:tabular-nums;text-align:right}}
.fdrop.none{{color:var(--line)}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel)}}
table{{width:100%;border-collapse:collapse;min-width:760px}}
thead th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);font-weight:650;padding:11px 14px;border-bottom:1px solid var(--line);background:#fbfbfc}}
td{{padding:11px 14px;vertical-align:top;border-bottom:1px solid var(--line2);font-size:13.5px}}
tbody tr:last-child td{{border-bottom:0}}tbody tr:hover td{{background:#fbfbfd}}
.co{{display:block;font-weight:620;font-size:13.5px}}
.dom{{display:block;font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--faint);text-decoration:none;margin-top:1px}}
.dom:hover{{color:var(--accent);text-decoration:underline}}
.pill{{display:inline-block;font-size:11.5px;font-weight:640;padding:3px 10px;border-radius:999px;white-space:nowrap}}
.pill.drop{{color:var(--drop);background:var(--drop-bg)}}.pill.unc{{color:var(--unc);background:var(--unc-bg)}}
.detail{{color:var(--soft);font-size:12.5px;max-width:46ch}}
.num{{font-variant-numeric:tabular-nums}}
.sh{{font-weight:660}}.emp{{font-size:11px;color:var(--faint)}}
.vertcell{{color:var(--soft);font-size:12px;max-width:30ch}}
.licell{{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:var(--faint)}}
.pok{{color:var(--pass);font-weight:620;font-size:12.5px;text-decoration:none}}.pok:hover{{text-decoration:underline}}
.pnone{{color:var(--line);font-weight:600}}
#q{{width:100%;max-width:340px;font:inherit;font-size:14px;padding:9px 13px;border:1px solid var(--line);border-radius:9px;background:var(--panel);margin-bottom:12px}}
#q:focus{{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}}
.note{{font-size:12.5px;color:var(--faint);margin:12px 2px 0}}
footer{{margin-top:44px;font-size:12px;color:var(--faint);border-top:1px solid var(--line);padding-top:14px}}
@media(max-width:820px){{.stats{{grid-template-columns:1fr 1fr}}h1{{font-size:25px}}.frow{{grid-template-columns:1fr;gap:5px}}.fdrop{{text-align:left}}}}
@media(prefers-reduced-motion:reduce){{.fbar{{transition:none}}}}
</style>
<div class="wrap">
<header class="masthead"><div class="mast-in">
<div class="eyebrow">Hyperline &middot; List building &middot; {e(today)}</div>
<h1>List qualification — {e(title)}</h1>
<p class="lede">{n_seed} seed companies run through the qualification pipeline (cleaning &rarr; ICP &rarr; Apollo/size &rarr; sales-motion). <b>{n_final} qualified</b> for the Pronto contact search; {n_dropped} dropped along the way, each with an auditable reason below.</p>
</div></header>

<section class="stats">
<div class="stat seed"><span class="num">{n_seed}</span><span class="lab">Seed companies</span></div>
<div class="stat q"><span class="num">{n_final}</span><span class="lab">Qualified — Pronto-ready ({100*n_final/max(n_seed,1):.0f}%)</span></div>
<div class="stat d"><span class="num">{n_dropped}</span><span class="lab">Dropped (all gates)</span></div>
<div class="stat pr"><span class="num">{n_pricing}</span><span class="lab">With a validated pricing page</span></div>
</section>

<h2>Funnel</h2>
<p class="h2sub">Companies surviving each gate. Red figures are drops at that stage.</p>
<div class="funnel">{''.join(funnel_html)}</div>

<h2>Dropped companies ({n_dropped})</h2>
<p class="h2sub">Every exclusion with the gate that fired and why.</p>
<div class="tablewrap"><table><thead><tr><th>Company</th><th>Stage</th><th>Reason</th><th>Detail</th></tr></thead>
<tbody>{''.join(drop_rows)}</tbody></table></div>

<h2>Qualified — ready for Pronto ({n_final})</h2>
<p class="h2sub">Sorted by sales-motion headcount (sales + business development, Apollo).</p>
<input id="q" type="search" placeholder="Search company, domain, vertical&hellip;" autocomplete="off" aria-label="Filter qualified companies">
<div class="tablewrap"><table><thead><tr><th>Company</th><th>Sales team</th><th>Employees</th><th>ICP conf.</th><th>Vertical</th><th>Pricing</th><th>LinkedIn ID</th></tr></thead>
<tbody id="qrows">{''.join(q_rows)}</tbody></table></div>
{db_note}
{coverage_html}

<footer>Generated by <code>process/qualification_artifact.py</code> &middot; project <code>{e(p)}</code> &middot; next step: upload the Pronto import CSV as an account list and run the Sales Nav search (Step 3).</footer>
</div>
<script>
const q=document.getElementById('q'),rows=[...document.querySelectorAll('#qrows tr')];
q.addEventListener('input',()=>{{const v=q.value.trim().toLowerCase();
rows.forEach(r=>r.style.display=!v||r.textContent.toLowerCase().includes(v)?'':'none');}});
</script>
"""

    out = f("qualification_report.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"Funnel: {' -> '.join(str(s['n']) for s in stages)}")
    print(f"Dropped: {n_dropped} | Qualified: {n_final} | Valid pricing: {n_pricing}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
