#!/usr/bin/env python3
"""
step4_contact_refinement.py — Generic contact refinement
=========================================================
Input:  One or more Pronto export CSVs + optional DB contacts CSV
Output: {project_name}_step4_contacts_filtered.csv (single output)

Logic:
  1. Load all input CSVs and merge
  2. Cross-reference against company list (match by domain or LinkedIn URL)
  3. Apply title inclusion/exclusion rules (substring matching)
  4. Categorize contacts: RevOps > Finance > COO > Founder
  5. Remove Founders from companies with >50 employees
  6. Keep max 2 contacts per company (by priority)
  7. Deduplicate by LinkedIn URL then email
  8. Write single output CSV

Usage:
    python3 step4_contact_refinement.py <project_name> \\
        --companies <step2_companies.csv> \\
        --contacts <pronto_export1.csv> [<pronto_export2.csv> ...] \\
        [--db-contacts <step2b_db_contacts.csv>]
"""

import csv, os, re, sys
from collections import Counter

# ── Config ────────────────────────────────────────────────────────────────────
MAX_PER_COMPANY = 2
FOUNDER_MAX_EMPLOYEES = 50

# ── Priority categories (order matters — lower index = higher priority) ──────
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

# ── Exclusion keywords (substring match — exclude even if inclusion matches) ─
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

# Short keywords that need word-boundary matching (≤3 chars)
SHORT_KEYWORDS = {"dg", "daf", "pdg", "coo", "ceo", "cfo", "cro", "dga",
                  "cto", "cpo", "cmo", "cdo", "cio", "vc"}


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


def categorize(title):
    """Return (category_name, priority_index) or None if no match."""
    title_lower = title.lower()

    if is_excluded(title_lower):
        return None

    for priority, (cat_name, keywords) in enumerate(CATEGORIES):
        for kw in keywords:
            if keyword_in_title(kw.lower(), title_lower):
                return (cat_name, priority)

    return None


def get_domain(row):
    """Extract company domain from row."""
    for key in ('Company Domain', 'Company Website', 'company_website', 'domain'):
        d = row.get(key, '').strip().lower()
        d = re.sub(r'^https?://', '', d).strip('/')
        if d:
            return d
    return ''


def get_company_linkedin(row):
    """Extract company LinkedIn URL, normalized."""
    for key in ('Company Linkedin Flagship Url', 'Company Linkedin Id Url',
                'Company Linkedin', 'company_linkedin_url'):
        url = row.get(key, '').strip().lower()
        if url:
            return re.sub(r'[?&].*$', '', url.rstrip('/'))
    return ''


def get_employee_count(row):
    """Extract employee count as int, or None."""
    for key in ('Employee Count', 'Estimated Employees', 'employee_count'):
        val = row.get(key, '').strip().replace(',', '')
        if val:
            try:
                return int(float(val))
            except ValueError:
                pass
    return None


def get_linkedin_profile(row):
    """Extract contact LinkedIn profile URL, normalized."""
    for key in ('Linkedin Profile Url', 'linkedin_url', 'LinkedIn'):
        url = row.get(key, '').strip().lower()
        if url:
            return re.sub(r'[?&].*$', '', url.rstrip('/'))
    return ''


def get_email(row):
    """Extract contact email."""
    return (row.get('Email', '') or row.get('email', '')).strip().lower()


def get_title(row):
    """Extract job title."""
    return (row.get('Title', '') or row.get('title', '') or row.get('Job Title', '')).strip()


def load_csv(path):
    """Load CSV, skip empty rows."""
    with open(path, newline='', encoding='utf-8-sig') as f:
        return [row for row in csv.DictReader(f) if any(v.strip() for v in row.values())]


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 step4_contact_refinement.py <project_name> "
              "--companies <file> --contacts <file> [<file>...] [--db-contacts <file>]")
        sys.exit(1)

    project_name = sys.argv[1]
    companies_path = None
    contact_paths = []
    db_contacts_path = None

    # Parse args
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '--companies' and i + 1 < len(args):
            companies_path = args[i + 1]
            i += 2
        elif args[i] == '--contacts':
            i += 1
            while i < len(args) and not args[i].startswith('--'):
                contact_paths.append(args[i])
                i += 1
        elif args[i] == '--db-contacts' and i + 1 < len(args):
            db_contacts_path = args[i + 1]
            i += 2
        else:
            i += 1

    if not companies_path or not contact_paths:
        print("Error: --companies and --contacts are required.")
        sys.exit(1)

    # ── Load company list ────────────────────────────────────────────────────
    companies = load_csv(companies_path)
    company_domains = set()
    company_linkedins = set()
    for row in companies:
        d = get_domain(row)
        if d:
            company_domains.add(d)
        li = get_company_linkedin(row)
        if li:
            company_linkedins.add(li)

    print("=" * 60)
    print(f"Step 4 — Contact Refinement: {project_name}")
    print("=" * 60)
    print(f"  Company list: {len(companies)} companies ({len(company_domains)} domains)")

    # ── Load contacts ────────────────────────────────────────────────────────
    all_contacts = []
    for path in contact_paths:
        rows = load_csv(path)
        print(f"  Loaded {len(rows)} contacts from {os.path.basename(path)}")
        all_contacts.extend(rows)

    if db_contacts_path:
        db_rows = load_csv(db_contacts_path)
        print(f"  Loaded {len(db_rows)} DB contacts from {os.path.basename(db_contacts_path)}")
        all_contacts.extend(db_rows)

    print(f"  Total contacts to process: {len(all_contacts)}")

    # ── Deduplicate by LinkedIn URL, then email ──────────────────────────────
    seen_li = set()
    seen_email = set()
    deduped = []
    for row in all_contacts:
        li = get_linkedin_profile(row)
        email = get_email(row)
        if li:
            if li in seen_li:
                continue
            seen_li.add(li)
        elif email:
            if email in seen_email:
                continue
            seen_email.add(email)
        else:
            continue  # no identifier
        deduped.append(row)

    print(f"  After dedup: {len(deduped)} contacts")

    # ── Filter: company membership ───────────────────────────────────────────
    in_company = []
    not_in_company = 0
    for row in deduped:
        domain = get_domain(row)
        co_li = get_company_linkedin(row)
        if domain in company_domains or co_li in company_linkedins:
            in_company.append(row)
        else:
            not_in_company += 1

    print(f"  In company list: {len(in_company)} (dropped {not_in_company} non-matches)")

    # ── Categorize + apply inclusion/exclusion ───────────────────────────────
    categorized = []  # (priority, category, row)
    excluded_count = 0
    no_match_count = 0

    for row in in_company:
        title = get_title(row)
        result = categorize(title)
        if result is None:
            if is_excluded(title.lower()):
                excluded_count += 1
            else:
                no_match_count += 1
            continue
        cat_name, priority = result
        categorized.append((priority, cat_name, row))

    print(f"  Categorized: {len(categorized)} (excluded: {excluded_count}, no match: {no_match_count})")

    cat_counts = Counter(cat for _, cat, _ in categorized)
    for cat_name, _ in CATEGORIES:
        print(f"    {cat_name}: {cat_counts.get(cat_name, 0)}")

    # ── Filter founders at >50 employees ─────────────────────────────────────
    founder_removed = 0
    filtered = []
    for priority, cat_name, row in categorized:
        if cat_name == "Founder":
            emp = get_employee_count(row)
            if emp is not None and emp > FOUNDER_MAX_EMPLOYEES:
                founder_removed += 1
                continue
        filtered.append((priority, cat_name, row))

    if founder_removed:
        print(f"  Removed {founder_removed} founders at companies with >{FOUNDER_MAX_EMPLOYEES} employees")

    # ── Keep max 2 per company (by priority) ─────────────────────────────────
    # Sort by priority (lower = better)
    filtered.sort(key=lambda x: x[0])

    company_counts = {}  # domain → count
    final = []
    trimmed = 0

    for priority, cat_name, row in filtered:
        domain = get_domain(row)
        co_li = get_company_linkedin(row)
        company_key = domain or co_li or 'unknown'

        current = company_counts.get(company_key, 0)
        if current >= MAX_PER_COMPANY:
            trimmed += 1
            continue

        company_counts[company_key] = current + 1
        row['_priority_category'] = cat_name
        final.append(row)

    print(f"  After max {MAX_PER_COMPANY}/company: {len(final)} contacts (trimmed {trimmed})")

    # ── Final stats ──────────────────────────────────────────────────────────
    final_cats = Counter(row['_priority_category'] for row in final)
    print(f"\n  Final distribution:")
    for cat_name, _ in CATEGORIES:
        print(f"    {cat_name}: {final_cats.get(cat_name, 0)}")

    companies_covered = len(company_counts)
    print(f"\n  Companies covered: {companies_covered}/{len(company_domains)}")
    print(f"  Missing companies: {len(company_domains) - companies_covered}")

    # ── Write output ─────────────────────────────────────────────────────────
    base_dir = os.path.dirname(os.path.abspath(contact_paths[0]))
    output_path = os.path.join(base_dir, f"{project_name}_step4_contacts_filtered.csv")

    fieldnames = list(final[0].keys()) if final else []
    if '_priority_category' not in fieldnames and final:
        fieldnames.insert(0, '_priority_category')

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(final)

    print(f"\n  Output: {output_path}")
    print(f"  Total: {len(final)} contacts")
    print("\nDone.")


if __name__ == "__main__":
    main()
