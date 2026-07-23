#!/usr/bin/env python3
"""
step2a_sales_motion_filter.py — Sales-motion gate (Step 2a)
===========================================================
Input:  {project_name}_step2_companies_with_linkedin.csv (Step 2 output)
Output:
  - {project_name}/{project_name}_step2a_companies_sales_motion.csv  (KEPT companies, feeds Step 2b)
  - {project_name}/{project_name}_step2a_sales_motion_report.csv     (ALL companies + verdict, auditable)

Why this step (added 2026-07-02):
  Step 1d's "Dimension 2 — sales motion" was designed but never enforced because it
  needs headcount data a web crawl can't provide. Apollo (Step 2) DOES return a
  per-department headcount (`departmental_head_count`), so the sales-motion gate lives
  here — right after Apollo, before any paid Pronto per-contact work (Steps 3 & 5).

  Hyperline sells billing/revenue infrastructure. A company with a real commercial
  motion (a sales team, negotiated deals, contracts) has the complex billing needs
  Hyperline serves; a company with no sales motion at all (pure self-serve micro-SaaS,
  founder-only, non-commercial entity) is a weaker fit and floods Pronto with noise.

Logic (Apollo-only, hard-drop — decided 2026-07-02; threshold lowered to 1
on user decision the same day: only companies with ZERO sales+BD staff drop):
  For each company, look up `departmental_head_count` in the Apollo DB (join by domain)
  and compute sales_motion_headcount = sales + business_development.
    - headcount >= SALES_MOTION_MIN (default 1)      -> KEEP  (motion="strong")
    - headcount in 0..MIN-1 WITH Apollo data present -> DROP  (motion="weak")
    - Apollo data missing / unparseable              -> KEEP  (motion="unknown")
      (never drop on missing data — same rule as the ICP gate)

Usage:
    python3 step2a_sales_motion_filter.py <project_name> \\
        --input <step2_companies.csv> \\
        [--min 1]        # minimum sales+BD headcount to pass (default 1)
        [--flag-only]    # keep everything, only tag the verdict (no drops)
"""

import argparse
import csv
import json
import os
import sys

SALES_MOTION_MIN_DEFAULT = 1
# Departments that count as "sales motion". business_development = BDR/outbound motion.
SALES_DEPTS = ["sales", "business_development"]


def norm_domain(d: str) -> str:
    d = (d or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if d.startswith(p):
            d = d[len(p):]
    return d.rstrip("/").split("/")[0]


def parse_headcount(raw):
    """Return the {dept: count} dict from an Apollo departmental_head_count cell,
    or None if absent/unparseable."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw in ("{}", "[]", "null", "None"):
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and d else None


def sales_motion_count(hc: dict) -> int:
    total = 0
    for dept in SALES_DEPTS:
        try:
            total += int(hc.get(dept, 0) or 0)
        except (ValueError, TypeError):
            pass
    return total


def main():
    parser = argparse.ArgumentParser(description="Sales-motion gate (Step 2a).")
    parser.add_argument("project_name")
    parser.add_argument("--input", required=True, dest="input_file",
                        help="Step 2 companies CSV")
    parser.add_argument("--min", type=int, default=SALES_MOTION_MIN_DEFAULT,
                        help=f"Min sales+BD headcount to pass (default {SALES_MOTION_MIN_DEFAULT})")
    parser.add_argument("--flag-only", action="store_true",
                        help="Keep everything, only tag the verdict (no drops)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    project_dir = os.path.join(repo_root, args.project_name)
    apollo_db_path = os.path.join(repo_root, "database", "apollo_companies_database.csv")

    if not os.path.isdir(project_dir):
        os.makedirs(project_dir, exist_ok=True)

    input_path = args.input_file
    if not os.path.isabs(input_path):
        input_path = os.path.join(repo_root, input_path)
    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}")
        sys.exit(1)
    if not os.path.isfile(apollo_db_path):
        print(f"ERROR: Apollo DB not found: {apollo_db_path}")
        sys.exit(1)

    # ── Load Apollo headcount by domain ───────────────────────────────────────
    hc_by_domain = {}
    with open(apollo_db_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            dom = norm_domain(r.get("domain", ""))
            if not dom:
                continue
            hc = parse_headcount(r.get("departmental_head_count", ""))
            if hc is not None:
                hc_by_domain[dom] = hc  # last write wins (DB is upserted, newest last)

    # ── Read input companies ──────────────────────────────────────────────────
    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        in_cols = reader.fieldnames or []
        rows = list(reader)

    extra_cols = ["sales_headcount", "bd_headcount", "sales_motion_headcount",
                  "sales_motion", "sales_motion_reason"]
    out_cols = list(in_cols) + [c for c in extra_cols if c not in in_cols]

    kept, dropped = [], []
    for r in rows:
        dom = norm_domain(r.get("domain", ""))
        hc = hc_by_domain.get(dom)
        if hc is None:
            r["sales_headcount"] = ""
            r["bd_headcount"] = ""
            r["sales_motion_headcount"] = ""
            r["sales_motion"] = "unknown"
            r["sales_motion_reason"] = "no Apollo departmental headcount — kept (never drop on missing data)"
            kept.append(r)
            continue

        sales = int(hc.get("sales", 0) or 0)
        bd = int(hc.get("business_development", 0) or 0)
        motion = sales + bd
        r["sales_headcount"] = sales
        r["bd_headcount"] = bd
        r["sales_motion_headcount"] = motion

        if motion >= args.min:
            r["sales_motion"] = "strong"
            r["sales_motion_reason"] = f"sales+BD headcount={motion} (>= {args.min})"
            kept.append(r)
        else:
            r["sales_motion"] = "weak"
            r["sales_motion_reason"] = f"sales+BD headcount={motion} (< {args.min})"
            if args.flag_only:
                kept.append(r)
            else:
                dropped.append(r)

    # ── Write outputs ─────────────────────────────────────────────────────────
    kept_path = os.path.join(project_dir, f"{args.project_name}_step2a_companies_sales_motion.csv")
    report_path = os.path.join(project_dir, f"{args.project_name}_step2a_sales_motion_report.csv")

    with open(kept_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        w.writerows(kept)

    # report = all rows (kept + dropped), sorted so dropped surface first
    report_rows = sorted(rows, key=lambda r: (r.get("sales_motion") != "weak",
                                              r.get("sales_motion") != "unknown",
                                              r.get("company_name", "")))
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        w.writerows(report_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    n_strong = sum(1 for r in rows if r.get("sales_motion") == "strong")
    n_weak = sum(1 for r in rows if r.get("sales_motion") == "weak")
    n_unknown = sum(1 for r in rows if r.get("sales_motion") == "unknown")

    print("\n" + "=" * 60)
    print("Step 2a: Sales-Motion Gate")
    print("=" * 60)
    print(f"Input companies:     {len(rows)}")
    print(f"Min sales+BD to pass: {args.min}   (departments: {', '.join(SALES_DEPTS)})")
    print(f"Mode:                {'FLAG-ONLY (no drops)' if args.flag_only else 'HARD DROP'}")
    print("-" * 60)
    print(f"  strong  (>= {args.min}):        {n_strong}")
    print(f"  unknown (no Apollo data): {n_unknown}  -> kept")
    print(f"  weak    (< {args.min}):        {n_weak}  -> {'kept (flag-only)' if args.flag_only else 'DROPPED'}")
    print("-" * 60)
    print(f"  KEPT:    {len(kept)}")
    print(f"  DROPPED: {len(dropped)}")
    if dropped:
        print("\n  --- Dropped (no sales motion) ---")
        for r in dropped:
            print(f"    {r.get('company_name',''):40s} sales={r.get('sales_headcount')} bd={r.get('bd_headcount')}")
    print(f"\n  -> {kept_path}")
    print(f"  -> {report_path}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
