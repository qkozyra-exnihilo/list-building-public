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
    ("Finance", [
        "cfo", "chief financial officer", "chief accounting officer",
        "vp finance", "vice president finance", "head of finance",
        "finance director", "financial director", "director of finance",
        "group cfo", "acting cfo", "sevp finance",
        # Controller / Financial Controller dropped 2026-06-09 (kept only via a
        # qualifying finance-leadership title in a combined role).
        "vp financial operations", "head of financial operations",
        "director of financial planning", "vp fp&a", "head of fp&a",
        "finance manager",
        # French
        "directeur financier", "directrice financière",
        "directeur administratif et financier", "directrice administrative et financière",
        "daf", "responsable financier", "responsable financière",
        # NB: "contrôleur/contrôleuse de gestion" (FR controller) dropped 2026-06-09.
    ]),
    # Revenue (#2): leadership + Revenue Operations / RevOps, ALL levels.
    ("Revenue", [
        "revenue operations", "revops",
        "cro", "chief revenue officer",
        "vp revenue", "vp of revenue", "head of revenue",
        "revenue director", "director of revenue",
        "cco", "chief commercial officer",
    ]),
    # Ops (#3): general ops/COO + Sales Ops + Growth Ops + Business Ops, Head-of+ only.
    ("Ops", [
        "coo", "chief operating officer", "chief operations officer",
        "vp operations", "vice president operations", "head of operations",
        "director of operations", "operations director",
        "founding chief operating officer",
        "head of business operations", "vp business operations",
        "director of business operations", "business operations director",
        "head of sales operations", "vp sales operations", "vp of sales operations",
        "director of sales operations", "sales operations director",
        "head of growth operations", "vp growth operations",
        "director of growth operations", "growth operations director",
        # French
        "directeur des opérations", "directrice des opérations",
        "directeur opérationnel", "directrice opérationnelle",
        "directeur d'exploitation", "directrice d'exploitation",
    ]),
    # NB: Sales removed as a target persona 2026-06-16. Pure-sales leadership
    # (Head of Sales / VP Sales / Sales Director / CSO / Directeur Commercial) now
    # matches no inclusion and is neither pulled nor counted. Sales OPERATIONS stays
    # under Ops above; CCO / Chief Commercial Officer stays under Revenue above.
]

EXCLUSIONS = [
    # Too junior. NB: Revenue Operations roles (Manager/Analyst/Specialist) are NO
    # LONGER excluded — kept under Revenue at all levels. Bare "operations manager"
    # is dropped by matching no Head-of+ inclusion, not by an exclusion keyword.
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
    # Wrong operations. NB: sales operations & business operations are NOT excluded
    # anymore (kept at Head-of+ via the Ops category above).
    "it operations", "technical operations",
    "marketing operations",
    "devops", "dev ops",
    "back office", "responsable back-office",
    "head of ai operations", "head of people operations",
    "head of customer operations", "head of product operations",
    # NB: Founders / CEO / President / GM are dropped by matching NO inclusion
    # category (the Founder category was removed 2026-06). They are intentionally
    # NOT listed here as exclusions — that would outrank inclusions and wrongly kill
    # combined titles like "Chief Executive Officer & CFO" (a kept Finance contact).
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
    "cto", "cpo", "cmo", "cdo", "cio", "vc", "cco", "cso",
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
    """Return True if the title passes inclusion rules and is not excluded.

    Used for PULLING DB contacts through into step4 — broad acceptance across the
    three kept personas (Finance, Revenue, Ops). Founders/CEO/President/GM and pure
    Sales (dropped 2026-06-16) match no category and are not pulled. Coverage
    decisions use is_primary_persona().
    """
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


def is_primary_persona(title):
    """Return True if the title is a primary target persona (Finance, Revenue, Ops).

    Used for the "covered by DB" threshold check. Since Sales was dropped as a
    persona (2026-06-16), the three kept categories ARE the primary personas, so
    this now mirrors is_valid_contact. (Rule origin 2026-05-26, the Dust
    false-coverage bug — Sales/founders were once supplement-only; both are now
    dropped outright.)
    """
    if not title or not title.strip():
        return False
    title_lower = title.lower()

    if is_excluded(title_lower):
        return False

    for cat_name, keywords in CATEGORIES:
        for kw in keywords:
            if keyword_in_title(kw.lower(), title_lower):
                return True

    return False


# ── Helper functions ──────────────────────────────────────────────────────────

def normalize_linkedin_url(url):
    """Normalize LinkedIn company URL: lowercase, strip protocol/www/query/trailing /,
    decode percent-encoding (Pronto stores e.g. l%27addition; found 2026-07-02 —
    protocol+encoding mismatches made DB coverage silently return 0)."""
    if not url or not url.strip():
        return ''
    from urllib.parse import unquote
    url = unquote(url.strip().lower())
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\.', '', url)
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
    E.g. https://www.linkedin.com/company/acme -> acme
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


# Apollo's `linkedin_uid` field is supposed to be the LinkedIn company numeric ID,
# but Apollo sometimes populates it with their own internal organization_id
# (24-character MongoDB ObjectId hex like "673082adcd1c1f0001827f97"). Pushing
# that into the Pronto "LinkedIn ID" column either fails to resolve or matches
# the wrong entity (see Skeepers/Foederis/etc. — Pronto-export audit 2026-05-26).
APOLLO_ORG_ID_RE = re.compile(r'^[0-9a-f]{24}$')


def sanitize_linkedin_uid(uid, linkedin_url=''):
    """
    Return a value safe to send as Pronto's "LinkedIn ID" column.
    - Valid numeric LinkedIn IDs (digits only): pass through.
    - Slugs (letters/digits/hyphens, not 24-char hex): pass through.
    - Apollo org_ids (24-char hex): drop and fall back to URL slug.
    - Empty / unrecognized: fall back to URL slug.
    """
    s = (uid or '').strip()
    if s and not APOLLO_ORG_ID_RE.match(s.lower()):
        return s
    return extract_linkedin_id(linkedin_url)


def main():
    parser = argparse.ArgumentParser(
        description="Check Pronto contacts DB for existing contacts, split companies "
                    "into those needing Pronto search vs those already covered."
    )
    parser.add_argument("project_name",
                        help="Project name (used for output folder and filename)")
    parser.add_argument("--input", required=True, dest="input_file",
                        help="Path to step2 companies CSV")
    parser.add_argument("--min-contacts", type=int, default=2, dest="min_contacts",
                        help="Primary-persona DB contacts to consider a company 'covered' "
                             "and skip Pronto search (default 2)")
    parser.add_argument("--max-coverage", action="store_true", dest="max_coverage",
                        help="Never skip Pronto: send every company to the Pronto import "
                             "to maximize contacts; reusable DB contacts are still pulled "
                             "along and deduped downstream (Step 4). Use when the ask is "
                             "'as many contacts as possible', not 'at least N'.")
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
                linkedin_uid = sanitize_linkedin_uid(
                    apollo_row.get('linkedin_uid', ''),
                    company.get('linkedin_company_url', ''),
                )
                pronto_import_rows.append({
                    'Company Name': company_name,
                    'Company Website': domain,
                    'LinkedIn URL': '',
                    'LinkedIn ID': linkedin_uid,
                })
            continue

        # Look up contacts in DB
        db_contacts_for_company = db_by_company.get(co_li, [])

        # All passing inclusion rules — these are pulled through to step4 regardless
        valid_contacts = [
            c for c in db_contacts_for_company
            if is_valid_contact(c.get('title', ''))
        ]
        # Only Finance/Revenue/Ops count toward "covered by DB" — Sales doesn't
        # qualify (supplement persona) because we still want senior finance/revenue/
        # ops leads at the same company. Origin: the Dust false-coverage bug
        # (2026-05-26); founders previously played this supplement role but are now
        # dropped entirely.
        primary_contacts = [
            c for c in db_contacts_for_company
            if is_primary_persona(c.get('title', ''))
        ]

        if not args.max_coverage and len(primary_contacts) >= args.min_contacts:
            # Covered by DB: pull ALL valid contacts (Sales included as supplement)
            covered_count += 1
            for c in valid_contacts:
                db_contact_rows.append(c)
        else:
            # Need Pronto search — and still pull any valid DB contacts (incl. Sales
            # supplement) along so they don't get lost. Step 4 dedups by LinkedIn URL.
            need_search_count += 1
            for c in valid_contacts:
                db_contact_rows.append(c)
            # Look up linkedin_uid from Apollo DB, sanitize (drops Apollo's
            # internal 24-char org_ids that masquerade as LinkedIn IDs)
            apollo_row = apollo_db.get(domain, {})
            linkedin_uid = sanitize_linkedin_uid(
                apollo_row.get('linkedin_uid', ''),
                company.get('linkedin_company_url', '') or co_li,
            )

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
