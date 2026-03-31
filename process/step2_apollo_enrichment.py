#!/usr/bin/env python3
"""
step2_apollo_enrichment.py — Enrich companies with LinkedIn URLs via Apollo API
================================================================================
Input:  {project_name}_step1b_companies_cleaned.csv or step1c_companies_verified.csv
Output: {project_name}/{project_name}_step2_companies_with_linkedin.csv

Logic:
  1. Load Apollo database (database/apollo_companies_database.csv) for cache lookup
  2. Match input companies by domain (case-insensitive)
  3. For DB hits: reuse linkedin_company_url, industry, naics_codes, keywords
  4. For DB misses: call Apollo bulk enrich API (batch of 10, 0.3s rate limit)
  5. Show cost estimate and ask for confirmation before API calls
  6. After enrichment: append new results to database
  7. Backfill industry, naics_code, naics_label, keywords from DB
  8. Apply ICP filter (unless --skip-icp-filter)
  9. Write output CSV

Usage:
    python3 step2_apollo_enrichment.py <project_name> \\
        --input <step1b_or_step1c.csv> \\
        [--skip-icp-filter]
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
from datetime import datetime


# ── Apollo API config ─────────────────────────────────────────────────────────
APOLLO_API_URL = "https://api.apollo.io/api/v1/organizations/bulk_enrich"
BATCH_SIZE = 10
RATE_LIMIT_SLEEP = 0.3
MAX_RETRIES = 4

# ── Apollo database columns ──────────────────────────────────────────────────
APOLLO_DB_COLUMNS = [
    "domain", "company_name", "linkedin_company_url", "linkedin_uid",
    "website_url", "primary_phone", "sanitized_phone",
    "founded_year", "industry", "industries", "naics_codes", "sic_codes",
    "estimated_num_employees", "organization_revenue", "organization_revenue_printed",
    "street_address", "city", "state", "country", "postal_code",
    "short_description", "twitter_url", "facebook_url", "keywords",
    "departmental_head_count", "headcount_6m_growth", "headcount_12m_growth",
    "headcount_24m_growth", "source_project", "enriched_date"
]

# ── ICP Filter: Target industries (case-insensitive) ─────────────────────────
TARGET_INDUSTRIES = [
    "information technology & services",
    "computer software",
    "computer & network security",
    "computer games",
    "computer hardware",
    "computer networking",
    "program development",
    "telecommunications",
    "information services",
]

# ── ICP Filter: NAICS prefixes ───────────────────────────────────────────────
TARGET_NAICS_PREFIXES = ["5132", "5112", "5182", "5415", "519", "517"]

# ── ICP Filter: Keywords ─────────────────────────────────────────────────────
INCLUDE_KEYWORDS = [
    "saas", "ai", "software", "cloud", "platform", "computer",
    "analytics", "automation", "artificial intelligence", "consulting",
]

EXCLUDE_KEYWORDS = [
    "non-profit", "billing", "food", "farming", "maritime", "sporting",
]

# ── NAICS 2-digit sector labels ──────────────────────────────────────────────
NAICS_2DIGIT_LABELS = {
    "11": "Agriculture, Forestry, Fishing and Hunting",
    "21": "Mining, Quarrying, and Oil and Gas Extraction",
    "22": "Utilities",
    "23": "Construction",
    "31": "Manufacturing",
    "32": "Manufacturing",
    "33": "Manufacturing",
    "42": "Wholesale Trade",
    "44": "Retail Trade",
    "45": "Retail Trade",
    "48": "Transportation and Warehousing",
    "49": "Transportation and Warehousing",
    "51": "Information",
    "52": "Finance and Insurance",
    "53": "Real Estate and Rental and Leasing",
    "54": "Professional, Scientific, and Technical Services",
    "55": "Management of Companies and Enterprises",
    "56": "Administrative and Support and Waste Management",
    "61": "Educational Services",
    "62": "Health Care and Social Assistance",
    "71": "Arts, Entertainment, and Recreation",
    "72": "Accommodation and Food Services",
    "81": "Other Services",
    "92": "Public Administration",
}

# ── NAICS 5-digit labels (common tech/SaaS codes) ────────────────────────────
NAICS_5DIGIT_LABELS = {
    "51121": "Software Publishers",
    "51130": "Book Publishers",
    "51210": "Motion Picture and Video Industries",
    "51321": "Software Publishers (2022)",
    "51322": "Software Publishers (2022)",
    "51820": "Data Processing, Hosting, and Related Services",
    "51913": "Internet Publishing and Broadcasting",
    "51919": "All Other Information Services",
    "51710": "Wired and Wireless Telecommunications",
    "54151": "Computer Systems Design and Related Services",
    "54171": "Research and Development in the Physical, Engineering, and Life Sciences",
}


def load_apollo_db(db_path: str) -> dict:
    """
    Load the Apollo companies database into a dict keyed by domain (lowercase).
    Returns empty dict if file does not exist.
    """
    db = {}
    if not os.path.isfile(db_path):
        print(f"  Apollo DB not found at {db_path}. Starting fresh.")
        return db

    with open(db_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            domain = row.get("domain", "").strip().lower()
            if domain:
                db[domain] = row

    print(f"  Apollo DB loaded: {len(db)} companies")
    return db


def append_to_apollo_db(db_path: str, new_rows: list):
    """Append new enrichment results to the Apollo database CSV."""
    if not new_rows:
        return

    file_exists = os.path.isfile(db_path)

    # Ensure directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with open(db_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=APOLLO_DB_COLUMNS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"  Appended {len(new_rows)} new rows to Apollo DB")


def call_apollo_bulk_enrich(domains: list, api_key: str) -> dict:
    """
    Call Apollo bulk enrich API for a batch of domains.
    Returns dict keyed by domain with organization data.
    """
    payload = json.dumps({"domains": domains}).encode("utf-8")

    req = urllib.request.Request(
        APOLLO_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
        method="POST",
    )

    retries = 0
    while retries <= MAX_RETRIES:
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode("utf-8"))
            results = {}
            for org in data.get("organizations", []):
                if org and org.get("primary_domain"):
                    d = org["primary_domain"].strip().lower()
                    results[d] = org
            return results
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retries += 1
                wait = 2 ** retries
                print(f"    Rate limited (429). Retry {retries}/{MAX_RETRIES} in {wait}s...")
                time.sleep(wait)
            else:
                print(f"    Apollo API error: HTTP {e.code}")
                try:
                    body = e.read().decode("utf-8")
                    print(f"    Response: {body[:300]}")
                except Exception:
                    pass
                return {}
        except Exception as e:
            print(f"    Apollo API error: {e}")
            return {}

    print(f"    Max retries exceeded for batch.")
    return {}


def parse_apollo_org(org: dict, domain: str, project_name: str) -> dict:
    """Convert an Apollo organization response to a database row."""
    linkedin_url = org.get("linkedin_url", "") or ""
    # Normalize LinkedIn URL
    if linkedin_url and not linkedin_url.startswith("http"):
        linkedin_url = f"https://www.linkedin.com/{linkedin_url}"

    # Extract keywords as semicolon-separated string
    keywords_list = org.get("keywords", []) or []
    if isinstance(keywords_list, list):
        keywords = "; ".join(str(k) for k in keywords_list)
    else:
        keywords = str(keywords_list)

    # Extract industries
    industries_list = org.get("industries", []) or []
    if isinstance(industries_list, list):
        industries = "; ".join(str(i) for i in industries_list)
    else:
        industries = str(industries_list)

    # NAICS codes
    naics_list = org.get("naics_codes", []) or []
    if isinstance(naics_list, list):
        naics_codes = "; ".join(str(n) for n in naics_list)
    else:
        naics_codes = str(naics_list)

    # SIC codes
    sic_list = org.get("sic_codes", []) or []
    if isinstance(sic_list, list):
        sic_codes = "; ".join(str(s) for s in sic_list)
    else:
        sic_codes = str(sic_list)

    # Departmental head count
    dept_hc = org.get("departmental_head_count", {}) or {}
    if isinstance(dept_hc, dict):
        dept_hc_str = json.dumps(dept_hc)
    else:
        dept_hc_str = str(dept_hc)

    return {
        "domain": domain,
        "company_name": org.get("name", "") or "",
        "linkedin_company_url": linkedin_url,
        "linkedin_uid": org.get("id", "") or "",
        "website_url": org.get("website_url", "") or "",
        "primary_phone": org.get("primary_phone", {}).get("number", "") if isinstance(org.get("primary_phone"), dict) else (org.get("primary_phone", "") or ""),
        "sanitized_phone": org.get("primary_phone", {}).get("sanitized_number", "") if isinstance(org.get("primary_phone"), dict) else "",
        "founded_year": str(org.get("founded_year", "") or ""),
        "industry": org.get("industry", "") or "",
        "industries": industries,
        "naics_codes": naics_codes,
        "sic_codes": sic_codes,
        "estimated_num_employees": str(org.get("estimated_num_employees", "") or ""),
        "organization_revenue": str(org.get("organization_revenue", "") or ""),
        "organization_revenue_printed": org.get("organization_revenue_printed", "") or "",
        "street_address": org.get("street_address", "") or "",
        "city": org.get("city", "") or "",
        "state": org.get("state", "") or "",
        "country": org.get("country", "") or "",
        "postal_code": org.get("postal_code", "") or "",
        "short_description": org.get("short_description", "") or "",
        "twitter_url": org.get("twitter_url", "") or "",
        "facebook_url": org.get("facebook_url", "") or "",
        "keywords": keywords,
        "departmental_head_count": dept_hc_str,
        "headcount_6m_growth": str(org.get("headcount_6m_growth", "") or ""),
        "headcount_12m_growth": str(org.get("headcount_12m_growth", "") or ""),
        "headcount_24m_growth": str(org.get("headcount_24m_growth", "") or ""),
        "source_project": project_name,
        "enriched_date": datetime.now().strftime("%Y-%m-%d"),
    }


def get_naics_label(naics_code: str) -> str:
    """Derive a human-readable NAICS label from a code."""
    if not naics_code:
        return ""
    code = naics_code.strip()

    # Try 5-digit match first
    if len(code) >= 5:
        label = NAICS_5DIGIT_LABELS.get(code[:5])
        if label:
            return label

    # Fall back to 2-digit sector
    if len(code) >= 2:
        label = NAICS_2DIGIT_LABELS.get(code[:2])
        if label:
            return label

    return ""


def apply_icp_filter(rows: list) -> tuple:
    """
    Apply the ICP filter (industry/NAICS + keyword confirmation).
    Returns (kept_rows, dropped_rows, stats_dict).

    Stats dict has keys: confirmed_keywords, kept_no_keywords, kept_no_data, dropped.
    """
    kept = []
    dropped = []
    stats = {
        "confirmed_keywords": 0,
        "kept_no_keywords": 0,
        "kept_no_data": 0,
        "dropped": 0,
    }

    for row in rows:
        industry = row.get("industry", "").strip().lower()
        naics_code = row.get("naics_code", "").strip()
        keywords_str = row.get("keywords", "").strip().lower()

        # Check industry match
        industry_match = any(t == industry for t in TARGET_INDUSTRIES)

        # Check NAICS match
        naics_match = False
        if naics_code:
            for prefix in TARGET_NAICS_PREFIXES:
                if naics_code.startswith(prefix):
                    naics_match = True
                    break

        # Case 3: no industry AND no NAICS -> keep for manual review
        if not industry and not naics_code:
            stats["kept_no_data"] += 1
            kept.append(row)
            continue

        # No match on either layer
        if not industry_match and not naics_match:
            stats["dropped"] += 1
            dropped.append(row)
            continue

        # Industry or NAICS matches -> check keywords
        if not keywords_str:
            # No keywords available, can't verify -> keep
            stats["kept_no_keywords"] += 1
            kept.append(row)
            continue

        # Parse keywords
        kw_parts = [k.strip() for k in re.split(r"[;,]", keywords_str) if k.strip()]

        has_include = any(
            inc_kw in kw for inc_kw in INCLUDE_KEYWORDS if inc_kw != "consulting"
            for kw in kw_parts
        )
        has_consulting = any("consulting" in kw for kw in kw_parts)
        has_only_exclude = all(
            any(exc_kw in kw for exc_kw in EXCLUDE_KEYWORDS)
            for kw in kw_parts
        ) if kw_parts else False

        # Consulting special rule
        if has_consulting and not has_include:
            # "consulting" only, no include keyword -> drop
            stats["dropped"] += 1
            dropped.append(row)
            continue

        if has_include:
            stats["confirmed_keywords"] += 1
            kept.append(row)
            continue

        if has_only_exclude:
            stats["dropped"] += 1
            dropped.append(row)
            continue

        # Keywords exist but no include and no exclude -> keep (ambiguous)
        stats["kept_no_keywords"] += 1
        kept.append(row)

    return kept, dropped, stats


def main():
    parser = argparse.ArgumentParser(
        description="Enrich companies with LinkedIn URLs via Apollo API, with database caching."
    )
    parser.add_argument("project_name",
                        help="Project name (used for output folder and filename)")
    parser.add_argument("--input", required=True, dest="input_file",
                        help="Path to input CSV (step1b or step1c)")
    parser.add_argument("--skip-icp-filter", action="store_true", dest="skip_icp_filter",
                        help="Skip the ICP industry/NAICS/keyword filter")
    args = parser.parse_args()

    # ── Resolve paths ─────────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    project_dir = os.path.join(repo_root, args.project_name)
    db_dir = os.path.join(repo_root, "database")
    apollo_db_path = os.path.join(db_dir, "apollo_companies_database.csv")

    if not os.path.isdir(project_dir):
        os.makedirs(project_dir, exist_ok=True)
        print(f"Created project folder: {project_dir}")

    input_path = args.input_file
    if not os.path.isabs(input_path):
        input_path = os.path.join(repo_root, input_path)

    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}")
        sys.exit(1)

    output_path = os.path.join(
        project_dir, f"{args.project_name}_step2_companies_with_linkedin.csv"
    )

    # ── Read input ────────────────────────────────────────────────────────────
    companies = []
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        input_columns = list(reader.fieldnames or [])
        for row in reader:
            companies.append(row)

    total_input = len(companies)
    print(f"\n{'='*60}")
    print(f"Step 2: Apollo Enrichment")
    print(f"{'='*60}")
    print(f"Input: {input_path} ({total_input} companies)")

    # ── Load Apollo database ──────────────────────────────────────────────────
    print(f"\nLoading Apollo database...")
    apollo_db = load_apollo_db(apollo_db_path)

    # ── Categorize companies: DB hit, needs API, skip (no domain) ─────────────
    db_hits = []        # (index, row, db_row)
    needs_api = []      # (index, row, domain)
    skipped = []        # (index, row, reason)

    for i, row in enumerate(companies):
        domain = row.get("domain", "").strip().lower()
        domain_flag = row.get("domain_flag", "").strip().upper()

        if domain_flag == "MISSING_DOMAIN" or not domain:
            skipped.append((i, row, "MISSING_DOMAIN"))
            continue

        if domain in apollo_db:
            db_hits.append((i, row, apollo_db[domain]))
        else:
            needs_api.append((i, row, domain))

    print(f"\n  Companies in DB (cache hit):  {len(db_hits)}")
    print(f"  Companies needing API call:   {len(needs_api)}")
    print(f"  Companies skipped (no domain):{len(skipped)}")

    # ── Cost estimate and confirmation ────────────────────────────────────────
    new_db_rows = []

    if needs_api:
        print(f"\n--- Cost Estimate ---")
        print(f"  Companies to enrich: {len(needs_api)}")
        print(f"  Apollo credits: ~{len(needs_api)} credits (1 per company)")
        print(f"  API calls: {(len(needs_api) + BATCH_SIZE - 1) // BATCH_SIZE} batches of {BATCH_SIZE}")

        confirm = input(f"\n  Proceed with Apollo enrichment? (y/n): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("  Enrichment cancelled by user.")
            needs_api = []

    # ── Call Apollo API ───────────────────────────────────────────────────────
    api_key = os.environ.get("APOLLO_API_KEY", "")
    if needs_api and not api_key:
        print("ERROR: APOLLO_API_KEY environment variable not set.")
        sys.exit(1)

    api_results = {}  # domain -> apollo org data
    if needs_api:
        batches = [
            needs_api[i:i + BATCH_SIZE]
            for i in range(0, len(needs_api), BATCH_SIZE)
        ]
        print(f"\n  Enriching {len(needs_api)} companies in {len(batches)} batches...")

        for batch_idx, batch in enumerate(batches, 1):
            domains = [item[2] for item in batch]
            print(f"    Batch {batch_idx}/{len(batches)}: {len(domains)} domains", end="")

            results = call_apollo_bulk_enrich(domains, api_key)
            api_results.update(results)

            found = len(results)
            print(f" -> {found} found")

            if batch_idx < len(batches):
                time.sleep(RATE_LIMIT_SLEEP)

        # Parse API results into DB rows
        for domain, org_data in api_results.items():
            db_row = parse_apollo_org(org_data, domain, args.project_name)
            new_db_rows.append(db_row)
            apollo_db[domain] = db_row

        print(f"\n  API enrichment complete: {len(api_results)} companies found")

    # ── Append new results to Apollo database ─────────────────────────────────
    if new_db_rows:
        append_to_apollo_db(apollo_db_path, new_db_rows)

    # ── Backfill data from DB to companies ────────────────────────────────────
    print(f"\nBackfilling data from Apollo DB...")

    n_linkedin_filled = 0
    n_industry_filled = 0
    n_naics_filled = 0
    n_keywords_filled = 0

    for row in companies:
        domain = row.get("domain", "").strip().lower()
        if not domain or domain not in apollo_db:
            continue

        db_row = apollo_db[domain]

        # LinkedIn URL
        if not row.get("linkedin_company_url", "").strip():
            linkedin = db_row.get("linkedin_company_url", "").strip()
            if linkedin:
                row["linkedin_company_url"] = linkedin
                n_linkedin_filled += 1

        # Industry
        if not row.get("industry", "").strip():
            industry = db_row.get("industry", "").strip()
            if industry:
                row["industry"] = industry
                n_industry_filled += 1

        # NAICS code (first from list)
        if not row.get("naics_code", "").strip():
            naics_codes = db_row.get("naics_codes", "").strip()
            if naics_codes:
                # Parse first code (semicolon or comma separated)
                first_code = re.split(r"[;,]", naics_codes)[0].strip()
                if first_code:
                    row["naics_code"] = first_code
                    row["naics_label"] = get_naics_label(first_code)
                    n_naics_filled += 1

        # Keywords
        if not row.get("keywords", "").strip():
            keywords = db_row.get("keywords", "").strip()
            if keywords:
                row["keywords"] = keywords
                n_keywords_filled += 1

    print(f"  LinkedIn URLs filled:  {n_linkedin_filled}")
    print(f"  Industries filled:     {n_industry_filled}")
    print(f"  NAICS codes filled:    {n_naics_filled}")
    print(f"  Keywords filled:       {n_keywords_filled}")

    # ── Apply ICP filter ──────────────────────────────────────────────────────
    # Filter out MISSING_DOMAIN companies (they don't go to output)
    enrichable = [
        row for row in companies
        if row.get("domain_flag", "").strip().upper() != "MISSING_DOMAIN"
    ]

    if args.skip_icp_filter:
        print(f"\n  ICP filter: SKIPPED (--skip-icp-filter)")
        output_rows = enrichable
    else:
        print(f"\n  Applying ICP filter...")
        output_rows, dropped_rows, filter_stats = apply_icp_filter(enrichable)

        print(f"  ICP Filter Results:")
        print(f"    Kept (confirmed by keywords): {filter_stats['confirmed_keywords']}")
        print(f"    Kept (no keywords, can't verify): {filter_stats['kept_no_keywords']}")
        print(f"    Kept (no industry/NAICS data): {filter_stats['kept_no_data']}")
        print(f"    Dropped: {filter_stats['dropped']}")

        if dropped_rows:
            print(f"\n  --- Dropped companies (sample, max 20) ---")
            for row in dropped_rows[:20]:
                print(f"    {row.get('company_name', '')} | "
                      f"industry={row.get('industry', '')} | "
                      f"naics={row.get('naics_code', '')}")

    # ── Build output columns ──────────────────────────────────────────────────
    output_columns = list(input_columns)
    for col in ["linkedin_company_url", "industry", "naics_code", "naics_label", "keywords"]:
        if col not in output_columns:
            output_columns.append(col)

    # ── Write output ──────────────────────────────────────────────────────────
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Step 2: Apollo Enrichment — Summary")
    print(f"{'='*60}")
    print(f"Input companies:          {total_input}")
    print(f"DB cache hits:            {len(db_hits)}")
    print(f"API calls made:           {len(needs_api)}")
    print(f"API results found:        {len(api_results)}")
    print(f"New rows added to DB:     {len(new_db_rows)}")
    print(f"Skipped (no domain):      {len(skipped)}")
    if not args.skip_icp_filter:
        print(f"ICP filter kept:          {len(output_rows)}")
        print(f"ICP filter dropped:       {len(enrichable) - len(output_rows)}")
    print(f"Output rows:              {len(output_rows)}")
    print(f"Output: {output_path}")
    print(f"{'='*60}\n")

    # Check for companies with no LinkedIn URL (may need step 1c)
    no_linkedin = [
        row for row in output_rows
        if not row.get("linkedin_company_url", "").strip()
    ]
    if no_linkedin:
        print(f"WARNING: {len(no_linkedin)} companies have no LinkedIn URL after enrichment.")
        print(f"  Consider running step1c_domain_verification.py on the output to fix these.")
        print()


if __name__ == "__main__":
    main()
