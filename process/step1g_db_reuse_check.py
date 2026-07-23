#!/usr/bin/env python3
"""
step1g_db_reuse_check.py — Cross-source reuse check BEFORE any paid enrichment
==============================================================================
Purpose: before spending Apollo credits (Step 2) or Pronto credits (Step 5),
check EVERY data source we already own for these companies and their contacts,
so we never re-pay for data we have.

Sources checked:
  1. database/apollo_companies_database.csv   (company firmographics + LinkedIn URL)
  2. database/pronto_contacts_database.csv     (already-enriched contacts w/ email+phone)
  3. Supabase TAM (pricing-benchmark `companies` table) — pass an export via --tam-csv
        Produce the export with (from ~/github/research/pricing-benchmark):
          npx supabase db query --linked "SELECT domain, linkedin_url,
            current_billing_tool, current_payment_provider, current_crm, pricing_model
            FROM companies WHERE domain IN (...);" --output json  →  csv
  4. Attio CRM — pass an export via --attio-csv (columns: domain, in_attio,
        attio_status, account_owner, record_id). Attio also flags EXISTING
        CUSTOMERS / ACTIVE PIPELINE that must be EXCLUDED from a cold list.

Decision per company:
  - LinkedIn URL found in ANY source  → no Apollo org lookup needed
  - Valid-persona contacts already in Pronto DB → REUSE them for free (email+phone
    we already own); dedup Pronto results against them so we never re-bill.
  - Two contact strategies:
      * standard (default): >= --min-contacts valid DB contacts → skip Pronto search
      * --max-coverage: NEVER skip; run Pronto on every company to pull as many
        valid-persona contacts as possible, merging + deduping with DB contacts.
  - Attio status = customer / active deal → FLAG for exclusion (do not cold-prospect)

Usage:
    python3 step1g_db_reuse_check.py <project_name> \\
        --input <step1f_or_step1b.csv> \\
        --domain-col domain \\
        [--tam-csv <supabase_export.csv>] \\
        [--attio-csv <attio_export.csv>] \\
        [--max-coverage]        # enrich every company for max contacts (this run)

Output:
    {project}/{project}_step1g_reuse_check.csv   (per-company verdict)
    prints a funnel summary
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

csv.field_size_limit(10**7)

# Persona include/exclude — kept in sync with step2b / step4 title rules (summary).
INCL = ["cfo", "chief financial", "finance", "financial", "fp&a", "daf",
        "directeur financier", "directrice financ", "revenue", "revops", "cro",
        "chief revenue", "cco", "chief commercial", "coo", "chief operating",
        "operations"]
EXCL = ["controller", "contrôleur", "assistant", "intern", "stagiaire", "board",
        "investor", "advisor", "devops", "it operations", "marketing operation",
        "chief of staff", "managing director", "treasurer", "account executive",
        "sales development", "sdr", "bdr", "account manager", "customer success",
        "support", "recruit", "talent", "people", "engineer", "developer",
        "product", "design", "legal", "founder", "ceo", "president",
        "general manager"]


def norm(d: str) -> str:
    if not d:
        return ""
    d = d.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    return d.split("/")[0].split("?")[0].strip()


def persona_ok(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in INCL) and not any(k in t for k in EXCL)


def load_csv_dict(path, key_col, key_norm=True):
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k = norm(r.get(key_col, "")) if key_norm else r.get(key_col, "")
            if k:
                out[k] = r
    return out


def main():
    ap = argparse.ArgumentParser(description="Cross-source reuse check before enrichment.")
    ap.add_argument("project_name")
    ap.add_argument("--input", required=True)
    ap.add_argument("--domain-col", default="domain")
    ap.add_argument("--tam-csv", default="")
    ap.add_argument("--attio-csv", default="")
    ap.add_argument("--min-contacts", type=int, default=2,
                    help="valid-persona contacts in DB to consider a company covered (default 2)")
    ap.add_argument("--max-coverage", action="store_true",
                    help="never skip Pronto; enrich every company for max contacts, "
                         "reusing + deduping DB contacts")
    args = ap.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    db_dir = os.path.join(repo_root, "database")
    project_dir = os.path.join(repo_root, args.project_name)
    os.makedirs(project_dir, exist_ok=True)

    inp = args.input
    if not os.path.isabs(inp):
        inp = os.path.join(repo_root, inp) if os.path.isfile(os.path.join(repo_root, inp)) else inp
    if not os.path.isfile(inp):
        print(f"ERROR: input not found: {inp}")
        sys.exit(1)

    # our companies
    ours = {}
    with open(inp, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = norm(r.get(args.domain_col, ""))
            if d:
                ours[d] = r

    apollo = load_csv_dict(os.path.join(db_dir, "apollo_companies_database.csv"), "domain")
    tam = load_csv_dict(args.tam_csv, "domain")
    attio = load_csv_dict(args.attio_csv, "domain")

    pronto = defaultdict(list)
    ppath = os.path.join(db_dir, "pronto_contacts_database.csv")
    if os.path.isfile(ppath):
        with open(ppath, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                d = norm(r.get("company_website", ""))
                if d in ours:
                    pronto[d].append(r)

    def li_source(d):
        if apollo.get(d, {}).get("linkedin_company_url", "").strip():
            return "apollo"
        if tam.get(d, {}).get("linkedin_url", "").strip():
            return "tam"
        for c in pronto.get(d, []):
            if c.get("company_linkedin_url", "").strip():
                return "pronto"
        return ""

    rows = []
    n_li = n_covered = n_need_apollo = n_need_pronto = n_exclude = 0
    for d, r in ours.items():
        li = li_source(d)
        valid = [c for c in pronto.get(d, []) if persona_ok(c.get("title", ""))]
        # In max-coverage mode we never mark a company as "covered" — every company
        # still goes to Pronto to maximize contacts; DB contacts are reused + deduped.
        covered = (not args.max_coverage) and len(valid) >= args.min_contacts
        attio_status = (attio.get(d, {}).get("attio_status", "") or "").strip()
        exclude = attio_status.lower() in ("customer", "active", "won", "closed won", "in pipeline")
        crm = tam.get(d, {}).get("current_crm", "") or ""
        billing = tam.get(d, {}).get("current_billing_tool", "") or ""

        if li:
            n_li += 1
        else:
            n_need_apollo += 1
        if covered:
            n_covered += 1
        else:
            n_need_pronto += 1
        if exclude:
            n_exclude += 1

        rows.append({
            "domain": d,
            "company_name": r.get("company_name", ""),
            "abm_tier": r.get("abm_tier", ""),
            "linkedin_source": li or "NONE",
            "valid_db_contacts": len(valid),
            "covered_by_db_contacts": "yes" if covered else "no",
            "in_attio": attio.get(d, {}).get("in_attio", "unknown") if attio else "unchecked",
            "attio_status": attio_status or ("unchecked" if not attio else "unknown"),
            "attio_exclude": "YES" if exclude else "",
            "tam_crm": crm,
            "tam_billing_tool": billing,
            "needs_apollo": "no" if li else "yes",
            "needs_pronto": "no" if covered else "yes",
        })

    out = os.path.join(project_dir, f"{args.project_name}_step1g_reuse_check.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("=" * 64)
    print("Step 1g: Cross-source reuse check")
    print("=" * 64)
    reusable = sum(r["valid_db_contacts"] for r in rows)
    print(f"  Mode:                                   {'MAX-COVERAGE (enrich all)' if args.max_coverage else 'standard (skip at >=%d)' % args.min_contacts}")
    print(f"  Companies:                              {len(ours)}")
    print(f"  LinkedIn URL from a DB (no Apollo):     {n_li}")
    print(f"  Need Apollo org lookup (no LI anywhere):{n_need_apollo}")
    print(f"  Reusable valid-persona DB contacts:     {reusable} (free — dedup Pronto against these)")
    if not args.max_coverage:
        print(f"  Covered by DB contacts (skip Pronto):   {n_covered}")
    print(f"  Companies going to Pronto:              {n_need_pronto}")
    if attio:
        print(f"  Attio EXCLUDE (customer/active deal):   {n_exclude}")
    else:
        print(f"  Attio: not provided (--attio-csv) — customer/pipeline exclusion NOT checked")
    print(f"  Output: {out}")
    print("=" * 64)


if __name__ == "__main__":
    main()
