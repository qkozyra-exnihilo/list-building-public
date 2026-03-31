#!/usr/bin/env python3
"""
step2b_db_contact_check.py — Check Pronto contacts DB for existing contacts
============================================================================
Input:  {project_name}_step2_companies_with_linkedin.csv
Output:
  - {project_name}/{project_name}_step2b_pronto_import.csv  (companies needing Pronto search)
  - {project_name}/{project_name}_step2b_db_contacts.csv    (valid contacts pulled from DB)

Logic:
  1. Load Pronto contacts database (database/pronto_contacts_database.csv)
  2. For each company in input, match by linkedin_company_url (case-insensitive, strip trailing /)
  3. Apply same inclusion/exclusion title rules as Step 4 to count valid contacts in DB
  4. Decision:
     - >= 2 valid contacts in DB: skip Pronto search, pull contacts into db_contacts output
     - 0-1 valid contacts in DB: include in Pronto import CSV
     - Not in DB: include in Pronto import CSV
     - No linkedin_company_url: include in Pronto import if has domain
  5. Write output files + print summary

Usage:
    python3 step2b_db_contact_check.py <project_name> \\
        --input <step2_companies.csv>
"""

import argparse
import csv
import os
import re
import sys


# ── Title matching logic (same as step4_contact_refinement.py) ────────────────

CATEGORIES = [
    ("RevOps", [
        "revenue operations", "revops",
        "cro", "chief revenue officer",
        "vp revenue", "head of revenue", "revenue director", "director of revenue",
    ]),
    ("Finance", [
        "cfo", "chief financial officer", "chief accounting officer",
        "vp finance", "vice president finance", "head of finance",
        "finance director", "financial director", "director of finance",
        "group cfo", "acting cfo", "sevp finance",
        "controller", "financial controller",
        "vp financial operations", "head of financial operations",
        "director of financial planning", "vp fp&a", "head of fp&a",
        # French
        "directeur financier", "directrice financière",
        "directeur administratif et financier", "directrice administrative et financière",
        "daf", "responsable financier", "responsable financière",
        "contrôleur de gestion", "contrôleuse de gestion",
    ]),
    ("COO", [
        "coo", "chief operating officer", "chief operations officer",
        "vp operations", "vice president operations", "head of operations",
        "director of operations", "operations director",
        "general manager", "founding chief operating officer",
        # French
        "directeur des opérations", "directrice des opérations",
        "directeur opérationnel", "directrice opérationnelle",
        "directeur d'exploitation", "directrice d'exploitation",
    ]),
    ("Founder", [
        "founder", "co-founder", "cofounder",
        "ceo", "chief executive officer", "co-ceo",
        "president", "founding ceo", "founding partner", "entrepreneur",
        # French
        "directeur général", "directrice générale", "dg", "pdg",
        "président directeur général", "président", "présidente",
        "gérant", "gérante", "associé gérant",
        "cofondateur", "cofondatrice", "fondateur", "fondatrice",
        "co-fondateur", "co-fondatrice",
        "dirigeant", "dirigeante",
        "dga", "directeur général adjoint", "directrice générale adjointe",
    ]),
]

EXCLUSIONS = [
    # Too junior
    "revenue operations manager", "revenue operations associate",
    "revenue operations specialist", "revenue operations analyst",
    "revenue operations crm manager",
    "operations manager", "finance manager",
    "project manager", "program manager",
    "assistant", "assistante", "office manager",
    "responsable administratif et financier", "responsable administrative et financière",
    # Wrong C-suite
    "cto", "chief technology officer", "chief technical officer",
    "cpo", "chief product officer",
    "cmo", "chief marketing officer",
    "chief scientific officer",
    "chief data officer", "cdo",
    "chief information officer", "cio",
    "chief of staff",
    # Wrong operations
    "business operations", "it operations", "technical operations",
    "sales operations", "marketing operations",
    "devops", "dev ops",
    "back office", "responsable back-office",
    "head of ai operations", "head of people operations",
    "head of customer operations", "head of product operations",
    # Excluded leadership
    "managing director", "treasurer", "trésorier", "trésorière",
    # Deputy
    "deputy", "adjoint", "adjointe", "déléguée",
    # Board / investors
    "board member", "board director", "board observer", "board secretary",
    "investor", "private investor", "business angel",
    "advisor", "adviser", "conseil",
    "administrateur", "administratrice",
    "independent director", "non-executive",
    "mentor", "scout",
    # Fund
    "venture capital", "private equity", "pe fund",
    "fund manager", "general partner", "operating partner", "portfolio manager",
    # Fractional
    "fractional", "interim", "consultant", "freelance",
    # Project directors
    "project director", "directeur projet", "directeur de projet",
    "program director", "head of project management",
]

SHORT_KEYWORDS = {
    "dg", "daf", "pdg", "coo", "ceo", "cfo", "cro", "dga",
    "cto", "cpo", "cmo", "cdo", "cio", "vc",
}


def keyword_in_title(keyword, title_lower):
    """Check if keyword appears in title. Use word-boundary for short keywords."""
    if keyword in SHORT_KEYWORDS:
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', title_lower))
    return keyword in title_lower


def is_excluded(title_lower):
    """Return True if the title matches any exclusion keyword."""
    for exc in EXCLUSIONS:
        exc_lower = exc.lower()
        if exc_lower in SHORT_KEYWORDS:
            if keyword_in_title(exc_lower, title_lower):
                return True
        elif exc_lower in title_lower:
            return True
    return False


def is_valid_contact(title):
    """Return True if the title passes inclusion rules and is not excluded."""
    if not title or not title.strip():
        return False
    title_lower = title.lower()

    if is_excluded(title_lower):
        return False

    for _, keywords in CATEGORIES:
        for kw in keywords:
            if keyword_in_title(kw.lower(), title_lower):
                return True

    return False


# ── Helper functions ──────────────────────────────────────────────────────────

def normalize_linkedin_url(url):
    """Normalize LinkedIn company URL: lowercase, strip trailing /, remove query params."""
    if not url or not url.strip():
        return ''
    url = url.strip().lower()
    url = re.sub(r'[?&].*$', '', url)
    return url.rstrip('/')


def load_csv(path):
    """Load CSV, skip empty rows."""
    with open(path, newline='', encoding='utf-8-sig') as f:
        return [row for row in csv.DictReader(f) if any(v.strip() for v in row.values())]


def get_company_linkedin(row):
    """Extract company LinkedIn URL from input company row."""
    for key in ('linkedin_company_url', 'Company Linkedin Flagship Url',
                'Company Linkedin Id Url', 'Company Linkedin'):
        url = row.get(key, '').strip()
        if url:
            return normalize_linkedin_url(url)
    return ''


def get_domain(row):
    """Extract domain from row."""
    for key in ('domain', 'Company Website', 'Company Domain', 'company_website'):
        d = row.get(key, '').strip().lower()
        d = re.sub(r'^https?://', '', d).strip('/')
        if d:
            return d
    return ''


def get_company_name(row):
    """Extract company name from row."""
    return (row.get('company_name', '') or row.get('Company Name', '')).strip()


def extract_linkedin_id(url):
    """
    Extract LinkedIn ID from company URL.
    Looks for numeric ID first in apollo DB, falls back to slug from URL.
    E.g. https://www.linkedin.com/company/yourcompany -> yourcompany
    E.g. https://www.linkedin.com/company/12345 -> 12345
    """
    if not url:
        return ''
    # Strip query params and trailing slash
    url = re.sub(r'[?&].*$', '', url.strip().rstrip('/'))
    # Extract last path segment
    match = re.search(r'/company/([^/]+)$', url.lower())
    if match:
        return match.group(1)
    return ''


def main():
    parser = argparse.ArgumentParser(
        description="Check Pronto contacts DB for existing contacts, split companies "
                    "into those needing Pronto search vs those already covered."
    )
    parser.add_argument("project_name",
                        help="Project name (used for output folder and filename)")
    parser.add_argument("--input", required=True, dest="input_file",
                        help="Path to step2 companies CSV")
    args = parser.parse_args()

    # ── Resolve paths ─────────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    project_dir = os.path.join(repo_root, args.project_name)
    db_dir = os.path.join(repo_root, "database")
    pronto_db_path = os.path.join(db_dir, "pronto_contacts_database.csv")
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

    pronto_import_path = os.path.join(
        project_dir, f"{args.project_name}_step2b_pronto_import.csv"
    )
    db_contacts_path = os.path.join(
        project_dir, f"{args.project_name}_step2b_db_contacts.csv"
    )

    # ── Load input companies ──────────────────────────────────────────────────
    companies = load_csv(input_path)

    print(f"\n{'='*60}")
    print(f"Step 2b: DB Contact Quality Check")
    print(f"{'='*60}")
    print(f"Input: {input_path} ({len(companies)} companies)")

    # ── Load Apollo DB for linkedin_uid lookup ────────────────────────────────
    apollo_db = {}  # domain -> row
    if os.path.isfile(apollo_db_path):
        with open(apollo_db_path, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                domain = row.get('domain', '').strip().lower()
                if domain:
                    apollo_db[domain] = row
        print(f"  Apollo DB loaded: {len(apollo_db)} companies")

    # ── Load Pronto contacts database ─────────────────────────────────────────
    pronto_contacts = []
    if os.path.isfile(pronto_db_path):
        pronto_contacts = load_csv(pronto_db_path)
        print(f"  Pronto contacts DB loaded: {len(pronto_contacts)} contacts")
    else:
        print(f"  WARNING: Pronto DB not found at {pronto_db_path}")

    # Build index: normalized company_linkedin_url -> list of contact rows
    db_by_company = {}
    for contact in pronto_contacts:
        co_li = normalize_linkedin_url(contact.get('company_linkedin_url', ''))
        if co_li:
            db_by_company.setdefault(co_li, []).append(contact)

    print(f"  Unique companies in Pronto DB: {len(db_by_company)}")

    # ── Process each company ──────────────────────────────────────────────────
    pronto_import_rows = []   # companies needing Pronto search
    db_contact_rows = []      # valid contacts pulled from DB
    covered_count = 0
    need_search_count = 0
    no_linkedin_count = 0

    for company in companies:
        co_li = get_company_linkedin(company)
        domain = get_domain(company)
        company_name = get_company_name(company)

        # No LinkedIn URL: include in Pronto import if has domain
        if not co_li:
            no_linkedin_count += 1
            if domain:
                apollo_row = apollo_db.get(domain, {})
                linkedin_uid = apollo_row.get('linkedin_uid', '')
                pronto_import_rows.append({
                    'Company Name': company_name,
                    'Company Website': domain,
                    'LinkedIn URL': '',
                    'LinkedIn ID': linkedin_uid,
                })
            continue

        # Look up contacts in DB
        db_contacts_for_company = db_by_company.get(co_li, [])

        # Filter to valid contacts using title rules
        valid_contacts = [
            c for c in db_contacts_for_company
            if is_valid_contact(c.get('title', ''))
        ]

        if len(valid_contacts) >= 2:
            # Covered by DB: pull contacts
            covered_count += 1
            for c in valid_contacts:
                db_contact_rows.append(c)
        else:
            # Need Pronto search (0-1 valid contacts or not in DB)
            need_search_count += 1
            # Look up linkedin_uid from Apollo DB
            apollo_row = apollo_db.get(domain, {})
            linkedin_uid = apollo_row.get('linkedin_uid', '')
            # Fall back to extracting slug from URL if no numeric ID
            if not linkedin_uid:
                linkedin_uid = extract_linkedin_id(co_li)

            pronto_import_rows.append({
                'Company Name': company_name,
                'Company Website': domain,
                'LinkedIn URL': company.get('linkedin_company_url', '').strip(),
                'LinkedIn ID': linkedin_uid,
            })

    # ── Write Pronto import CSV ───────────────────────────────────────────────
    pronto_import_columns = ['Company Name', 'Company Website', 'LinkedIn URL', 'LinkedIn ID']
    with open(pronto_import_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=pronto_import_columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(pronto_import_rows)

    # ── Write DB contacts CSV ─────────────────────────────────────────────────
    if db_contact_rows:
        db_fieldnames = list(db_contact_rows[0].keys())
        with open(db_contacts_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=db_fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(db_contact_rows)
    else:
        # Write empty file with standard headers
        pronto_db_columns = [
            "status", "rejection_reasons", "first_name", "last_name", "gender",
            "email", "email_status", "phone", "linkedin_url", "linkedin_id_url",
            "profile_image_url", "location", "title",
            "years_in_position", "months_in_position", "years_in_company", "months_in_company",
            "company_name", "company_cleaned_name", "company_website", "company_location",
            "company_industry", "company_linkedin_url", "company_linkedin_id",
            "company_employee_range", "company_hq_city", "company_hq_country",
            "company_hq_postal", "company_hq_region", "company_description",
            "source_project", "searched_date", "enriched_date",
        ]
        with open(db_contacts_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=pronto_db_columns)
            writer.writeheader()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Step 2b: DB Contact Quality Check — Summary")
    print(f"{'='*60}")
    print(f"Total companies:            {len(companies)}")
    print(f"Covered by DB (>= 2):       {covered_count}")
    print(f"Need Pronto search:         {need_search_count}")
    print(f"No LinkedIn URL:            {no_linkedin_count}")
    print(f"DB contacts pulled:         {len(db_contact_rows)}")
    print(f"\nOutputs:")
    print(f"  Pronto import: {pronto_import_path} ({len(pronto_import_rows)} companies)")
    print(f"  DB contacts:   {db_contacts_path} ({len(db_contact_rows)} contacts)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
