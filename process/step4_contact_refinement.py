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
  4. Categorize contacts: Finance > Revenue > Ops
  5. Keep max 2 contacts per company (by priority)
  6. Deduplicate by LinkedIn URL then email
  7. Write single output CSV

Usage:
    python3 step4_contact_refinement.py <project_name> \\
        --companies <step2_companies.csv> \\
        --contacts <pronto_export1.csv> [<pronto_export2.csv> ...] \\
        [--db-contacts <step2b_db_contacts.csv>]
"""

import csv, os, re, sys
from collections import Counter

# ── Config ────────────────────────────────────────────────────────────────────
# New rule (2026-05): keep ALL relevant contacts; the per-company cap of 2 now
# lives in Step 5 (PHONE enrichment only). So Step 4 no longer caps by default.
MAX_PER_COMPANY = 0  # 0 = unlimited; overridable via --max-per-company N

# ── Priority categories (order matters — lower index = higher priority) ──────
# NOTE: as of the 2026-05 persona-filter overhaul, the LLM judge in
# persona_judge.py is the PRIMARY classifier. These keyword lists are kept as
# the OFFLINE FALLBACK (used with --no-llm or when the API errors per contact).
# Rules they encode (2026-06 reprioritization; Sales dropped 2026-06-16):
#   - Finance              : keep ALL levels incl. junior (Lead, Operations, Manager)
#   - Revenue              : revenue leadership (CRO/CCO/Head of Revenue) + RevOps, ALL levels
#   - Ops                  : COO/general ops + Sales Ops + Growth Ops + Business Ops, "Head of"+ only
#   - Founders/CEO/President/GM : NOT a persona — never kept (dropped in 2026-06)
#   - Pure Sales (Head of Sales/VP Sales/Sales Director/CSO/Directeur Commercial) :
#       NOT a persona — dropped 2026-06-16. NB: Sales OPERATIONS stays under Ops, and
#       CCO/Chief Commercial Officer stays under Revenue.
# Revenue Operations resolves to Revenue (#2, higher priority), not Ops.
PRIORITY_ORDER = ["Finance", "Revenue", "Ops"]

CATEGORIES = [
    ("Finance", [
        "cfo", "chief financial officer", "chief accounting officer",
        "vp finance", "vp of finance", "vice president finance", "head of finance",
        "finance director", "financial director", "director of finance",
        "group cfo", "acting cfo", "sevp finance",
        # NB: Controller / Financial Controller / Group Controller dropped 2026-06-09
        # (too operational/backward-looking). A controller who also holds a qualifying
        # finance-leadership title still matches via that title.
        "vp financial operations", "head of financial operations",
        "director of financial planning", "vp fp&a", "head of fp&a",
        "vp accounting", "vp of accounting",
        "head of accounting", "director of accounting",
        "finance manager",
        # junior finance kept (new rule)
        "finance lead", "finance operations", "finance & operations",
        "fp&a", "financial planning", "financial analyst", "finance analyst",
        # French
        "directeur financier", "directrice financière",
        "directeur administratif et financier", "directrice administrative et financière",
        "daf", "responsable financier", "responsable financière",
        "responsable administratif et financier", "responsable administrative et financière",
        # NB: "contrôleur/contrôleuse de gestion" (FR controller) dropped 2026-06-09.
    ]),
    ("Revenue", [
        # Revenue leadership
        "cro", "chief revenue officer",
        "vp revenue", "vp of revenue", "head of revenue",
        "revenue director", "director of revenue",
        "cco", "chief commercial officer",
        # Revenue Operations / RevOps — ALL levels (substring "revenue operations"
        # catches head/director/vp/manager/analyst variants).
        "revenue operations", "revops",
        "head of revenue operations", "director of revenue operations",
        "vp revenue operations", "vp of revenue operations",
    ]),
    ("Ops", [
        # General operations / COO — "Head of" level and above only.
        "coo", "chief operating officer", "chief operations officer",
        "vp operations", "vice president operations", "head of operations",
        "director of operations", "operations director",
        "founding chief operating officer",
        "head of business operations", "vp business operations",
        "director of business operations", "business operations director",
        # Sales Operations — "Head of" level and above only (NOT bare "sales operations",
        # which would also match sub-Head Sales Operations Managers).
        "head of sales operations", "vp sales operations", "vp of sales operations",
        "director of sales operations", "sales operations director",
        # Growth Operations — "Head of" level and above only.
        "head of growth operations", "vp growth operations",
        "director of growth operations", "growth operations director",
        # French
        "directeur des opérations", "directrice des opérations",
        "directeur opérationnel", "directrice opérationnelle",
        "directeur d'exploitation", "directrice d'exploitation",
    ]),
    # NB: Sales removed as a target persona 2026-06-16. Pure-sales leadership
    # (Head of Sales, VP Sales, Sales Director, CSO, Head of Commercial, Directeur
    # Commercial, Directeur des Ventes) now matches no inclusion and is dropped.
    # Sales OPERATIONS (Head-of+) stays under Ops above; CCO / Chief Commercial
    # Officer stays under Revenue above.
]

# ── Stage A: HARD-DROP noise (guaranteed non-personas) ───────────────────────
# These are removed BEFORE the LLM judge — they never have edge cases, so we
# don't waste a model call on them. Everything NOT hard-dropped goes to the LLM
# (so finance-ops-style titles are always judged, never pre-filtered out).
HARD_DROP = [
    # Board / investors / advisors
    "board member", "board director", "board observer", "board secretary",
    "board of director", "board of directors", "member board",
    "investor", "investisseur", "investisseuse", "private investor",
    "angel investor", "business angel", "strategic investor", "seed investor",
    "serie a investor", "proud investor",
    "advisor", "adviser", "conseil", "administrateur", "administratrice",
    "independent director", "non-executive", "non executive", "mentor", "scout",
    # Fund
    "venture capital", "private equity", "pe fund",
    "fund manager", "general partner", "operating partner", "portfolio manager",
    # Students / interns / fractional / freelance / consulting
    "student", "étudiant", "etudiant", "intern", "stagiaire", "internship",
    "fractional", "interim", "intérim", "consultant", "consultante", "freelance",
    # Wrong C-suite (never the buyer for billing/revenue)
    "cto", "chief technology officer", "chief technical officer",
    "cpo", "chief product officer", "chief scientific officer",
    "chief data officer", "cdo", "chief information officer", "cio",
    "chief of staff",
    # CMO is wrong c-suite too
    "cmo", "chief marketing officer",
]

# ── Exclusion keywords (FALLBACK only — substring match) ──────────────────────
# Used only by the deterministic fallback categorize() (--no-llm / API error).
# Junior Finance/RevOps are intentionally NOT here anymore (new rule keeps them).
EXCLUSIONS = [
    # Too junior (non-finance/revops). NB: bare "operations manager" is NOT listed
    # because it is a substring of "revenue operations manager" (a kept RevOps
    # title); a plain Operations Manager simply matches no inclusion and is dropped.
    "project manager", "program manager",
    "assistant", "assistante", "office manager",
    # Wrong C-suite
    "cto", "chief technology officer", "chief technical officer",
    "cpo", "chief product officer",
    "cmo", "chief marketing officer",
    "chief scientific officer",
    "chief data officer", "cdo",
    "chief information officer", "cio",
    "chief of staff",
    # Wrong operations. NB: "sales operations" / "business operations" are NOT
    # excluded anymore — Sales Ops and general/Business Ops are KEPT at Head-of+
    # (their Head-of+ variants are inclusion keywords in the Ops category, while
    # sub-Head variants simply match no inclusion and drop). Listing them here
    # would wrongly kill "Head of Sales Operations" since exclusions outrank
    # inclusions in this fallback.
    "it operations", "technical operations",
    "marketing operations",
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
    "board of director", "board of directors", "member board",
    "investor", "private investor", "business angel",
    "advisor", "adviser", "conseil",
    "administrateur", "administratrice",
    "independent director", "non-executive", "non executive",
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
                  "cto", "cpo", "cmo", "cdo", "cio", "vc", "cco", "cso"}


def keyword_in_title(keyword, title_lower):
    """Check if keyword appears in title. Use word-boundary for short keywords."""
    if keyword in SHORT_KEYWORDS:
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', title_lower))
    return keyword in title_lower


def is_hard_drop(title_lower):
    """Stage A: guaranteed non-persona noise that never needs the LLM."""
    for kw in HARD_DROP:
        if kw in SHORT_KEYWORDS:
            if keyword_in_title(kw, title_lower):
                return True
        elif kw in title_lower:
            return True
    return False


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
        # Drop common subdomain prefixes (fr., en., eu1., www., etc.)
        d = re.sub(r'^(?:www|fr|en|de|es|it|nl|us|uk|eu\d?|app|mail|m|web|go)\.', '', d)
        if d:
            return d
    return ''


def get_company_linkedin(row):
    """Extract company LinkedIn URL, normalized (strip protocol + www. + trailing slash + query)."""
    for key in ('Company Linkedin Flagship Url', 'Company Linkedin Id Url',
                'Company Linkedin', 'company_linkedin_url', 'linkedin_company_url'):
        url = row.get(key, '').strip().lower()
        if url:
            url = re.sub(r'^https?://', '', url)
            url = re.sub(r'^www\.', '', url)
            url = re.sub(r'[?#].*$', '', url).rstrip('/')
            return url
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


def get_headline(row):
    """Extract LinkedIn headline (richer than the bare title)."""
    return (row.get('Linkedin Headline', '') or row.get('linkedin_headline', '')).strip()


def get_summary(row):
    """Extract a profile/role description that reveals real scope."""
    for key in ('Title Description', 'Summary', 'title_description', 'summary'):
        v = (row.get(key, '') or '').strip()
        if v:
            return v
    return ''


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
    max_per_company = MAX_PER_COMPANY  # may be overridden by --max-per-company
    use_llm = True                     # --no-llm forces deterministic fallback

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
        elif args[i] == '--max-per-company' and i + 1 < len(args):
            try:
                max_per_company = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i] == '--no-llm':
            use_llm = False
            i += 1
        else:
            i += 1

    if not companies_path or not contact_paths:
        print("Error: --companies and --contacts are required.")
        sys.exit(1)

    # ── Load company list ────────────────────────────────────────────────────
    companies = load_csv(companies_path)
    company_domains = set()
    company_linkedins = set()
    company_li_to_key = {}  # company LinkedIn URL → canonical key (domain, else the URL)
    for row in companies:
        d = get_domain(row)
        if d:
            company_domains.add(d)
        li = get_company_linkedin(row)
        if li:
            company_linkedins.add(li)
            company_li_to_key[li] = d or li

    def canonical_company_key(row):
        """Resolve a contact row to the company list's canonical key (domain first,
        else the company LinkedIn URL). Contacts that matched the list via LinkedIn
        (export row has no domain) must count under the SAME key as the company row,
        otherwise coverage/uncovered reports disagree (TIMCI/Amblea/Wishibam bug,
        found 2026-07-02)."""
        d = get_domain(row)
        if d and d in company_domains:
            return d
        cli = get_company_linkedin(row)
        if cli in company_li_to_key:
            return company_li_to_key[cli]
        return d or cli or 'unknown'

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

    # ── Drop rows with no first or last name (placeholder/garbage from DB) ───
    before_blank = len(all_contacts)
    all_contacts = [
        r for r in all_contacts
        if (r.get('First Name', '') or '').strip() or (r.get('Last Name', '') or '').strip()
    ]
    if before_blank != len(all_contacts):
        print(f"  Dropped blank-name rows: {before_blank - len(all_contacts)}")

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

    # ── Zero-leads alarm: which companies returned 0 leads from Pronto? ──────
    # Background: when an Apollo LinkedIn slug is stale (e.g. Skeepers cached
    # as "s-keeper"), the Pronto account-list scope silently misfires and we
    # see 0 leads for that company. Surface those companies here so the user
    # can verify slugs BEFORE marking them as "no targets exist."
    companies_with_leads = set()
    for row in deduped:
        d = get_domain(row)
        cli = get_company_linkedin(row)
        if d in company_domains:
            companies_with_leads.add(d)
        if cli in company_linkedins:
            # Map back to a canonical key — for reporting, prefer the domain
            for co_row in companies:
                if get_company_linkedin(co_row) == cli:
                    cd = get_domain(co_row)
                    if cd:
                        companies_with_leads.add(cd)
                    break

    zero_lead_companies = []
    for co_row in companies:
        d = get_domain(co_row)
        if d and d not in companies_with_leads:
            name = (co_row.get('company_name') or co_row.get('Company Name') or '').strip()
            li = get_company_linkedin(co_row)
            zero_lead_companies.append((name, d, li))

    if zero_lead_companies:
        print()
        print(f"  ZERO-LEADS ALARM: {len(zero_lead_companies)} companies in the list returned 0 Pronto leads.")
        print(f"  Verify their LinkedIn slugs — a wrong slug silently misfires the Pronto scope.")
        for name, d, li in zero_lead_companies[:30]:
            print(f"    {name:<28} domain={d:<25} linkedin={li}")
        if len(zero_lead_companies) > 30:
            print(f"    ... and {len(zero_lead_companies) - 30} more")
        print()

    # ── Stage A: deterministic noise removal (no model call) ─────────────────
    # Drop guaranteed non-personas (investors, board, students, wrong C-suite…).
    # Everything else proceeds to the LLM judge so finance-ops-style titles are
    # NEVER pre-filtered out by a missing keyword.
    survivors = []
    hard_dropped = 0
    for row in in_company:
        if is_hard_drop(get_title(row).lower()):
            hard_dropped += 1
            continue
        survivors.append(row)
    print(f"  Stage A noise removed: {hard_dropped}  →  {len(survivors)} to judge")

    # ── Stage B: LLM persona judge (primary), with deterministic fallback ────
    categorized = []  # (priority, category, row)
    kept_by_llm = 0
    kept_by_fallback = 0
    dropped_by_judge = 0

    def stamp(row, cat_name, fit, tier, reason, judged_by):
        row['_priority_category'] = cat_name
        row['_fit_score'] = fit
        row['_seniority_tier'] = tier
        row['_judge_reason'] = reason
        row['_judged_by'] = judged_by

    def deterministic_keep(row):
        """Fallback categorizer → (priority, cat, fit, tier) or None."""
        res = categorize(get_title(row))
        if res is None:
            return None
        cat_name, priority = res
        # crude fit proxy so phone-ranking still works without the LLM
        fit = 90 - priority * 10
        return priority, cat_name, fit, "keyword"

    verdicts = None
    if use_llm and survivors:
        try:
            import persona_judge
            cache_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "database", "persona_judgments.csv")
            items = [{
                "title": get_title(r), "headline": get_headline(r),
                "summary": get_summary(r),
                "company": (r.get("Company Name") or r.get("Company Cleaned Name") or ""),
                "emp": get_employee_count(r), "location": r.get("Location", ""),
            } for r in survivors]
            verdicts = persona_judge.judge_contacts(items, cache_path)
        except Exception as e:  # noqa: BLE001 — fall back wholesale if the layer fails
            print(f"  ! LLM judge unavailable ({e}); using deterministic fallback")
            verdicts = None

    for idx, row in enumerate(survivors):
        v = verdicts[idx] if verdicts is not None else None

        if v is not None and v.get("keep") is True and v.get("category") in PRIORITY_ORDER:
            cat_name = v["category"]
            priority = PRIORITY_ORDER.index(cat_name)
            stamp(row, cat_name, v.get("fit_score", 50),
                  v.get("seniority_tier", ""), v.get("reason", ""), "llm")
            categorized.append((priority, cat_name, row))
            kept_by_llm += 1
        elif v is not None and v.get("keep") is False:
            dropped_by_judge += 1
        else:
            # No verdict (API/parse error for this row, or --no-llm) → fallback
            fb = deterministic_keep(row)
            if fb is None:
                dropped_by_judge += 1
            else:
                priority, cat_name, fit, _ = fb
                stamp(row, cat_name, fit, "", "", "keyword")
                categorized.append((priority, cat_name, row))
                kept_by_fallback += 1

    print(f"  Judged keeps: {len(categorized)} "
          f"(llm={kept_by_llm}, fallback={kept_by_fallback}, dropped={dropped_by_judge})")

    cat_counts = Counter(cat for _, cat, _ in categorized)
    for cat_name in PRIORITY_ORDER:
        print(f"    {cat_name}: {cat_counts.get(cat_name, 0)}")

    # ── Keep max N per company (by priority + fit); N=0 means unlimited ──────
    # Sort by persona priority (lower = better), then by LLM fit_score (higher =
    # better) so the per-company cap keeps the MOST relevant N — not the first N
    # in export order. This mirrors Step 5's phone-eligibility ranking.
    def _fit(row):
        try:
            return int(float(row.get('_fit_score') or 0))
        except (TypeError, ValueError):
            return 0
    filtered = list(categorized)
    filtered.sort(key=lambda x: (x[0], -_fit(x[2])))

    company_counts = {}  # domain → count
    final = []
    trimmed = 0

    for priority, cat_name, row in filtered:
        company_key = canonical_company_key(row)

        current = company_counts.get(company_key, 0)
        if max_per_company > 0 and current >= max_per_company:
            trimmed += 1
            continue

        company_counts[company_key] = current + 1
        row['_priority_category'] = cat_name
        final.append(row)

    if max_per_company > 0:
        print(f"  After max {max_per_company}/company: {len(final)} contacts (trimmed {trimmed})")
    else:
        print(f"  No per-company cap applied: {len(final)} contacts kept")

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

    # ── Uncovered-companies report (ALWAYS written) ──────────────────────────
    # Every company in the input list that ends up with 0 kept contacts, split
    # by root cause so the gap is actionable rather than a single bare number:
    #   - zero_pronto_leads   : Pronto returned NO leads at all (verify slug /
    #                           broaden the Sales Nav search / company too small)
    #   - leads_all_filtered  : had leads but none matched a target persona
    #                           (only founders / sales / eng / wrong roles)
    covered_keys = set(company_counts.keys())
    leads_per_key = {}
    for row in deduped:
        k = canonical_company_key(row)
        if k != 'unknown' and (k in company_domains or k in company_li_to_key.values()):
            leads_per_key[k] = leads_per_key.get(k, 0) + 1

    uncovered_rows = []
    for co_row in companies:
        d = get_domain(co_row)
        li = get_company_linkedin(co_row)
        name = (co_row.get('company_name') or co_row.get('Company Name') or '').strip()
        key = d or li or 'unknown'
        if key in covered_keys:
            continue
        n_leads = leads_per_key.get(key, 0)
        status = 'zero_pronto_leads' if n_leads == 0 else 'leads_all_filtered'
        uncovered_rows.append({'company_name': name, 'domain': d, 'linkedin_url': li,
                               'pronto_leads': n_leads, 'status': status})

    uncovered_path = os.path.join(base_dir, f"{project_name}_uncovered_companies.csv")
    with open(uncovered_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['company_name', 'domain', 'linkedin_url',
                                          'pronto_leads', 'status'])
        w.writeheader()
        w.writerows(uncovered_rows)

    n_zero = sum(1 for r in uncovered_rows if r['status'] == 'zero_pronto_leads')
    n_filt = sum(1 for r in uncovered_rows if r['status'] == 'leads_all_filtered')
    print(f"\n  Uncovered companies: {len(uncovered_rows)}/{len(company_domains)} "
          f"({n_zero} zero Pronto leads, {n_filt} leads-but-all-filtered)")
    print(f"  Uncovered CSV: {uncovered_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
