#!/usr/bin/env python3
"""
step1c_domain_verification.py — AI-assisted domain and LinkedIn URL verification
==================================================================================
Input:  {project_name}_step1b_companies_cleaned.csv (pre-Apollo)
        OR {project_name}_step2_companies_with_linkedin.csv (post-Apollo)
Output: {project_name}/{project_name}_step1c_verification.csv (correction table)
        {project_name}/{project_name}_step1c_companies_verified.csv (corrected companies)

Logic:
  1. Read input CSV
  2. Identify companies needing verification:
     - domain_flag = "MISSING_DOMAIN"
     - No linkedin_company_url (empty or missing)
     - Domain looks suspicious (optional heuristic)
  3. For each flagged company, search for correct domain + LinkedIn URL
  4. Output a verification CSV with corrections
  5. Apply corrections and write verified companies file

Usage:
    python3 step1c_domain_verification.py <project_name> \\
        --input <step1b_or_step2.csv>

    # Apply a previously generated verification CSV without re-searching:
    python3 step1c_domain_verification.py <project_name> \\
        --input <step1b_or_step2.csv> \\
        --corrections <step1c_verification.csv>
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse


# ── Suspicious domain patterns ────────────────────────────────────────────────
SUSPICIOUS_TLDS = [".xyz", ".tk", ".ml", ".ga", ".cf", ".gq"]
SUSPICIOUS_SUBDOMAINS = ["mail.", "app.", "go.", "m.", "web."]


def is_suspicious_domain(domain: str) -> bool:
    """Check if a domain looks suspicious (wrong TLD, subdomain leftover, etc.)."""
    if not domain:
        return False
    d = domain.strip().lower()
    # Check suspicious TLDs
    for tld in SUSPICIOUS_TLDS:
        if d.endswith(tld):
            return True
    # Check leftover subdomains (should have been cleaned in step1b but catch stragglers)
    for sub in SUSPICIOUS_SUBDOMAINS:
        if d.startswith(sub):
            return True
    # Check if domain has too many dots (possible subdomain issue)
    parts = d.split(".")
    if len(parts) > 3:
        return True
    return False


def needs_verification(row: dict) -> tuple:
    """
    Determine if a company needs verification.
    Returns (needs_check: bool, reasons: list[str]).
    """
    reasons = []

    # Check domain_flag
    if row.get("domain_flag", "").strip().upper() == "MISSING_DOMAIN":
        reasons.append("MISSING_DOMAIN")

    # Check linkedin_company_url
    linkedin_url = row.get("linkedin_company_url", "").strip()
    if not linkedin_url:
        reasons.append("NO_LINKEDIN_URL")

    # Check suspicious domain
    domain = row.get("domain", "").strip()
    if domain and is_suspicious_domain(domain):
        reasons.append("SUSPICIOUS_DOMAIN")

    return (len(reasons) > 0, reasons)


def search_company_info(company_name: str, current_domain: str) -> dict:
    """
    Search for a company's correct domain and LinkedIn URL using web search.

    This function uses a basic approach with urllib. For production use,
    consider integrating a proper search API (Google Custom Search, SerpAPI, etc.)
    or using AI-assisted search tools.

    Returns dict with: correct_domain, linkedin_company_url, status, notes
    """
    result = {
        "correct_domain": "",
        "linkedin_company_url": "",
        "status": "NOT_FOUND",
        "notes": ""
    }

    # Strategy 1: Try LinkedIn search via Google
    linkedin_url = _search_linkedin_url(company_name)
    if linkedin_url:
        result["linkedin_company_url"] = linkedin_url

    # Strategy 2: Try to find the company website
    domain = _search_company_domain(company_name, current_domain)
    if domain:
        result["correct_domain"] = domain

    # Determine status
    if result["correct_domain"] or result["linkedin_company_url"]:
        result["status"] = "FOUND"
        parts = []
        if result["correct_domain"] and result["correct_domain"] != current_domain:
            parts.append(f"Domain corrected from '{current_domain}' to '{result['correct_domain']}'")
        if result["linkedin_company_url"]:
            parts.append("LinkedIn URL found")
        result["notes"] = "; ".join(parts)
    else:
        result["notes"] = "No results found via automated search. Manual verification needed."

    return result


def _search_linkedin_url(company_name: str) -> str:
    """
    Attempt to find a LinkedIn company page URL.
    Uses a simple Google search query via urllib.

    NOTE: Google blocks automated requests. This is a placeholder.
    For production, use SerpAPI, Google Custom Search API, or manual lookup.
    """
    # Construct a LinkedIn URL guess based on company name
    slug = re.sub(r"[^a-z0-9]", "-", company_name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    candidate = f"https://www.linkedin.com/company/{slug}"

    # Try to verify the URL exists
    try:
        req = urllib.request.Request(
            candidate,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            method="HEAD"
        )
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            return candidate
    except Exception:
        pass

    return ""


def _search_company_domain(company_name: str, current_domain: str) -> str:
    """
    Attempt to find a company's correct domain.

    NOTE: This is a placeholder. Automated Google search is blocked.
    For production, use a search API or manual lookup.
    Returns empty string if no result found.
    """
    # If current domain exists and is not suspicious, keep it
    if current_domain and not is_suspicious_domain(current_domain):
        return current_domain
    return ""


def load_verification_csv(path: str) -> list:
    """Load a previously generated verification CSV."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def apply_corrections(companies: list, corrections: list) -> tuple:
    """
    Apply corrections from the verification CSV to the companies list.
    Match by company_name (case-insensitive).
    Returns (updated_companies, n_applied).
    """
    # Build correction lookup by company name
    correction_map = {}
    for c in corrections:
        name = c.get("company_name", "").strip().lower()
        if name and c.get("status", "").strip().upper() == "FOUND":
            correction_map[name] = c

    n_applied = 0
    for row in companies:
        name = row.get("company_name", "").strip().lower()
        if name in correction_map:
            corr = correction_map[name]

            # Apply domain correction
            correct_domain = corr.get("correct_domain", "").strip()
            if correct_domain:
                row["domain"] = correct_domain
                # Clear the MISSING_DOMAIN flag if domain was found
                if row.get("domain_flag", "").strip().upper() == "MISSING_DOMAIN":
                    row["domain_flag"] = ""

            # Apply LinkedIn URL
            linkedin_url = corr.get("linkedin_company_url", "").strip()
            if linkedin_url:
                row["linkedin_company_url"] = linkedin_url

            n_applied += 1

    return companies, n_applied


def main():
    parser = argparse.ArgumentParser(
        description="AI-assisted domain and LinkedIn URL verification for companies missing data."
    )
    parser.add_argument("project_name", help="Project name (used for output folder and filename)")
    parser.add_argument("--input", required=True, dest="input_file",
                        help="Path to input CSV (step1b or step2)")
    parser.add_argument("--corrections", dest="corrections_file", default=None,
                        help="Path to a previously generated verification CSV to apply without re-searching")
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

    verification_output = os.path.join(
        project_dir, f"{args.project_name}_step1c_verification.csv"
    )
    companies_output = os.path.join(
        project_dir, f"{args.project_name}_step1c_companies_verified.csv"
    )

    # ── Read input ────────────────────────────────────────────────────────────
    companies = []
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        input_columns = list(reader.fieldnames or [])
        for row in reader:
            companies.append(row)

    total_input = len(companies)
    print(f"\nLoaded {total_input} companies from {input_path}")

    # ── Mode: apply existing corrections ──────────────────────────────────────
    if args.corrections_file:
        corrections_path = args.corrections_file
        if not os.path.isabs(corrections_path):
            corrections_path = os.path.join(repo_root, corrections_path)

        if not os.path.isfile(corrections_path):
            print(f"ERROR: corrections file not found: {corrections_path}")
            sys.exit(1)

        print(f"Applying corrections from: {corrections_path}")
        corrections = load_verification_csv(corrections_path)
        companies, n_applied = apply_corrections(companies, corrections)

        # Write verified output
        output_columns = list(input_columns)
        if "linkedin_company_url" not in output_columns:
            output_columns.append("linkedin_company_url")

        with open(companies_output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(companies)

        print(f"\n{'='*60}")
        print(f"Step 1c: Domain Verification (corrections applied)")
        print(f"{'='*60}")
        print(f"Corrections loaded:   {len(corrections)}")
        print(f"Corrections applied:  {n_applied}")
        print(f"Output:               {companies_output}")
        print(f"{'='*60}\n")
        return

    # ── Mode: search and verify ───────────────────────────────────────────────

    # Identify companies needing verification
    flagged = []
    for row in companies:
        check, reasons = needs_verification(row)
        if check:
            flagged.append((row, reasons))

    print(f"Companies needing verification: {len(flagged)}")

    if not flagged:
        print("No companies need verification. Copying input to output as-is.")
        output_columns = list(input_columns)
        if "linkedin_company_url" not in output_columns:
            output_columns.append("linkedin_company_url")

        with open(companies_output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(companies)

        print(f"Output: {companies_output}")
        return

    # Search for each flagged company
    verification_rows = []
    n_found = 0
    n_not_found = 0

    print(f"\nSearching for {len(flagged)} companies...")
    print("-" * 60)

    for i, (row, reasons) in enumerate(flagged, 1):
        company_name = row.get("company_name", "").strip()
        current_domain = row.get("domain", "").strip()

        print(f"  [{i}/{len(flagged)}] {company_name} ({', '.join(reasons)})", end="")

        result = search_company_info(company_name, current_domain)

        verification_rows.append({
            "company_name": company_name,
            "current_domain": current_domain,
            "correct_domain": result["correct_domain"],
            "linkedin_company_url": result["linkedin_company_url"],
            "status": result["status"],
            "notes": result["notes"],
        })

        if result["status"] == "FOUND":
            n_found += 1
            print(f" -> FOUND")
        else:
            n_not_found += 1
            print(f" -> NOT_FOUND")

        # Rate limit: avoid hammering external services
        if i < len(flagged):
            time.sleep(0.5)

    # ── Write verification CSV ────────────────────────────────────────────────
    verification_columns = [
        "company_name", "current_domain", "correct_domain",
        "linkedin_company_url", "status", "notes"
    ]

    with open(verification_output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=verification_columns)
        writer.writeheader()
        writer.writerows(verification_rows)

    print(f"\nVerification CSV written: {verification_output}")

    # ── Apply corrections to companies ────────────────────────────────────────
    companies, n_applied = apply_corrections(companies, verification_rows)

    # Write verified output
    output_columns = list(input_columns)
    if "linkedin_company_url" not in output_columns:
        output_columns.append("linkedin_company_url")

    with open(companies_output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(companies)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Step 1c: Domain & LinkedIn Verification")
    print(f"{'='*60}")
    print(f"Input:                {input_path}")
    print(f"Total companies:      {total_input}")
    print(f"Companies checked:    {len(flagged)}")
    print(f"  Found:              {n_found}")
    print(f"  Not found:          {n_not_found}")
    print(f"Corrections applied:  {n_applied}")
    print(f"{'='*60}")
    print(f"Verification CSV:     {verification_output}")
    print(f"Verified companies:   {companies_output}")
    print(f"{'='*60}")
    print()
    print("TIP: Review the verification CSV and manually fix NOT_FOUND entries.")
    print("     Then re-run with --corrections to apply your edits:")
    print(f"     python3 step1c_domain_verification.py {args.project_name} \\")
    print(f"         --input {args.input_file} \\")
    print(f"         --corrections {verification_output}")
    print()


if __name__ == "__main__":
    main()
