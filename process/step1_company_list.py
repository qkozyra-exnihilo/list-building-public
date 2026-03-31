#!/usr/bin/env python3
"""
step1_company_list.py — Standardize a seed CSV into a step1 company list
========================================================================
Input:  A seed CSV with company data (any column names)
Output: {project_name}/{project_name}_step1_companies.csv (standardized columns)

Logic:
  1. Read input CSV
  2. Map columns from seed format to standard format via --column-map
  3. Optionally carry forward extra columns from the seed via --extra-cols
  4. Write output with standard column order
  5. Print summary

Usage:
    python3 step1_company_list.py <project_name> \\
        --input <seed.csv> \\
        --column-map name=company_name,domain=domain \\
        [--extra-cols sector,nace_code,naics_code]

Column map format: comma-separated key=value pairs where
  key   = column name in the seed CSV
  value = standard column name in the output

Standard output columns:
  company_name, domain, linkedin_url, country_code, city,
  employee_count, employee_count_range, industry, founded_year,
  funding_stage, total_funding_usd, annual_revenue_usd
"""

import argparse
import csv
import os
import sys

# ── Standard output columns (order preserved) ────────────────────────────────
STANDARD_COLUMNS = [
    "company_name",
    "domain",
    "linkedin_url",
    "country_code",
    "city",
    "employee_count",
    "employee_count_range",
    "industry",
    "founded_year",
    "funding_stage",
    "total_funding_usd",
    "annual_revenue_usd",
]


def parse_column_map(raw: str) -> dict:
    """Parse 'seed_col=standard_col,seed_col2=standard_col2' into a dict."""
    mapping = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            print(f"WARNING: skipping malformed mapping '{pair}' (expected key=value)")
            continue
        src, dst = pair.split("=", 1)
        src, dst = src.strip(), dst.strip()
        if dst not in STANDARD_COLUMNS:
            print(f"WARNING: target column '{dst}' is not a standard column; mapping anyway")
        mapping[src] = dst
    return mapping


def main():
    parser = argparse.ArgumentParser(
        description="Standardize a seed CSV into a step1 company list."
    )
    parser.add_argument("project_name", help="Project name (used for output folder and filename)")
    parser.add_argument("--input", required=True, dest="input_file", help="Path to seed CSV")
    parser.add_argument(
        "--column-map",
        required=True,
        dest="column_map",
        help="Comma-separated key=value pairs mapping seed columns to standard columns",
    )
    parser.add_argument(
        "--extra-cols",
        dest="extra_cols",
        default="",
        help="Comma-separated list of additional seed columns to carry forward as-is",
    )
    args = parser.parse_args()

    # ── Resolve paths ─────────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    project_dir = os.path.join(repo_root, args.project_name)

    if not os.path.isdir(project_dir):
        os.makedirs(project_dir, exist_ok=True)
        print(f"Created project folder: {project_dir}")

    input_path = args.input_file
    if not os.path.isabs(input_path):
        input_path = os.path.join(repo_root, input_path)

    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}")
        sys.exit(1)

    output_filename = f"{args.project_name}_step1_companies.csv"
    output_path = os.path.join(project_dir, output_filename)

    # ── Parse mappings ────────────────────────────────────────────────────────
    col_map = parse_column_map(args.column_map)
    extra_cols = [c.strip() for c in args.extra_cols.split(",") if c.strip()] if args.extra_cols else []

    # Build full output column list
    output_columns = list(STANDARD_COLUMNS)
    for ec in extra_cols:
        if ec not in output_columns:
            output_columns.append(ec)

    # ── Read input ────────────────────────────────────────────────────────────
    rows_out = []
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        seed_columns = reader.fieldnames or []

        # Validate mappings exist in seed
        for src in col_map:
            if src not in seed_columns:
                print(f"WARNING: mapped source column '{src}' not found in seed CSV")
        for ec in extra_cols:
            if ec not in seed_columns:
                print(f"WARNING: extra column '{ec}' not found in seed CSV")

        # Build reverse map: standard_col -> seed_col
        reverse_map = {dst: src for src, dst in col_map.items()}

        for row in reader:
            out_row = {}
            # Fill standard columns from mapping
            for col in STANDARD_COLUMNS:
                if col in reverse_map:
                    out_row[col] = row.get(reverse_map[col], "").strip()
                else:
                    out_row[col] = ""

            # Fill extra columns directly (same name in seed and output)
            for ec in extra_cols:
                out_row[ec] = row.get(ec, "").strip()

            rows_out.append(out_row)

    # ── Write output ──────────────────────────────────────────────────────────
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns)
        writer.writeheader()
        writer.writerows(rows_out)

    # ── Summary ───────────────────────────────────────────────────────────────
    mapped_cols = [c for c in STANDARD_COLUMNS if c in reverse_map]
    unmapped_cols = [c for c in STANDARD_COLUMNS if c not in reverse_map]

    print(f"\n{'='*60}")
    print(f"Step 1: Company List Standardization")
    print(f"{'='*60}")
    print(f"Input:   {input_path}")
    print(f"Output:  {output_path}")
    print(f"Total rows:       {len(rows_out)}")
    print(f"Mapped columns:   {', '.join(mapped_cols)}")
    if unmapped_cols:
        print(f"Unmapped (empty): {', '.join(unmapped_cols)}")
    if extra_cols:
        print(f"Extra columns:    {', '.join(extra_cols)}")
    print(f"Output columns:   {len(output_columns)} total")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
