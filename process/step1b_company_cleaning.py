#!/usr/bin/env python3
"""
step1b_company_cleaning.py — Clean and deduplicate a company list before enrichment
====================================================================================
Input:  {project_name}_step1_companies.csv (from step1)
Output: {project_name}/{project_name}_step1b_companies_cleaned.csv

Logic:
  1. Fix malformed domains: strip common subdomains (mail., www., app., m., web., go.)
  2. Deduplicate by domain (case-insensitive, keep first occurrence)
  3. Flag missing domains: add domain_flag = "MISSING_DOMAIN" for companies with no domain
  4. Report all changes
  5. Write cleaned output

Usage:
    python3 step1b_company_cleaning.py <project_name> \\
        --input <step1_companies.csv>
"""

import argparse
import csv
import os
import re
import sys

# ── Subdomains to strip from the front of domains ────────────────────────────
STRIP_SUBDOMAINS = ["mail.", "www.", "app.", "m.", "web.", "go."]


def clean_domain(domain: str) -> str:
    """Strip common subdomains from a domain. Returns cleaned domain (lowercase)."""
    d = domain.strip().lower()
    # Remove protocol if present
    d = re.sub(r"^https?://", "", d)
    # Remove trailing slash or path
    d = d.split("/")[0]

    for prefix in STRIP_SUBDOMAINS:
        if d.startswith(prefix):
            stripped = d[len(prefix):]
            # Only strip if something remains after the prefix
            if "." in stripped:
                d = stripped
                break  # only strip one prefix

    return d


def main():
    parser = argparse.ArgumentParser(
        description="Clean and deduplicate a company list before enrichment."
    )
    parser.add_argument("project_name", help="Project name (used for output folder and filename)")
    parser.add_argument("--input", required=True, dest="input_file", help="Path to step1 companies CSV")
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

    output_filename = f"{args.project_name}_step1b_companies_cleaned.csv"
    output_path = os.path.join(project_dir, output_filename)

    # ── Read input ────────────────────────────────────────────────────────────
    rows = []
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        input_columns = list(reader.fieldnames or [])
        for row in reader:
            rows.append(row)

    total_input = len(rows)

    # ── Step 1: Fix malformed domains ─────────────────────────────────────────
    domains_fixed = []
    for row in rows:
        original = row.get("domain", "").strip()
        if original:
            cleaned = clean_domain(original)
            if cleaned != original.strip().lower():
                domains_fixed.append((row.get("company_name", ""), original, cleaned))
                row["domain"] = cleaned
            else:
                row["domain"] = cleaned

    # ── Step 2: Deduplicate by domain (case-insensitive, keep first) ──────────
    seen_domains = set()
    deduped_rows = []
    duplicates_removed = []

    for row in rows:
        domain = row.get("domain", "").strip().lower()
        if not domain:
            # Keep rows with no domain (they will be flagged in step 3)
            deduped_rows.append(row)
            continue
        if domain in seen_domains:
            duplicates_removed.append((row.get("company_name", ""), domain))
        else:
            seen_domains.add(domain)
            deduped_rows.append(row)

    # ── Step 3: Flag missing domains ──────────────────────────────────────────
    missing_domain = []
    for row in deduped_rows:
        domain = row.get("domain", "").strip()
        if not domain:
            row["domain_flag"] = "MISSING_DOMAIN"
            missing_domain.append(row.get("company_name", ""))
        else:
            row["domain_flag"] = ""

    # ── Build output columns ──────────────────────────────────────────────────
    output_columns = list(input_columns)
    if "domain_flag" not in output_columns:
        output_columns.append("domain_flag")

    # ── Write output ──────────────────────────────────────────────────────────
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped_rows)

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Step 1b: Company List Cleaning")
    print(f"{'='*60}")
    print(f"Input:   {input_path}")
    print(f"Output:  {output_path}")
    print(f"")
    print(f"Total input rows:     {total_input}")
    print(f"Domains fixed:        {len(domains_fixed)}")
    print(f"Duplicates removed:   {len(duplicates_removed)}")
    print(f"Missing domains:      {len(missing_domain)}")
    print(f"Output rows:          {len(deduped_rows)}")
    print(f"{'='*60}")

    if domains_fixed:
        print(f"\n--- Domains Fixed ---")
        for company, old, new in domains_fixed:
            print(f"  {company}: {old} -> {new}")

    if duplicates_removed:
        print(f"\n--- Duplicates Removed ---")
        for company, domain in duplicates_removed:
            print(f"  {company} ({domain})")

    if missing_domain:
        print(f"\n--- Missing Domains (flagged) ---")
        for company in missing_domain:
            print(f"  {company}")

    print()


if __name__ == "__main__":
    main()
