#!/usr/bin/env python3
"""
step6_final_output.py — Final output with optional phone filter + website backfill
===================================================================================
Input:  {project}_step5_contacts_enriched.csv
Output: {project}_FINAL.csv (single output)

Logic:
  1. Load step5 enriched contacts
  2. Optionally backfill Company Website from step2 company list
  3. Optionally filter phones by country prefix (e.g. +33 for France)
  4. Drop internal columns, keep only useful outreach columns
  5. Write single FINAL.csv

Usage:
    python3 step6_final_output.py <project_name> \
        --input <step5_contacts_enriched.csv> \
        [--companies <step2_companies.csv>]   \
        [--phone-prefix +33]
"""

import csv, os, re, sys
from collections import Counter


# ── Output columns (ordered for outreach use) ────────────────────────────────
OUTPUT_FIELDS = [
    "_priority_category",
    "First Name", "Last Name", "Title",
    "Email", "Email Status", "Phone (Pronto)",
    "Company Name", "Employee Range",
    "Company Industry", "Company Website",
    "Linkedin Profile Url",
    "Company Linkedin Flagship Url",
    "Location", "Contact Country",
    "Company HQ Country",
    "Technology Matched",
    "NAICS Code",
    "Status",
]

# ── Phone prefix → country code mapping ──────────────────────────────────────
PHONE_PREFIX_TO_COUNTRY = [
    ("+33", "FR"), ("+1", "US"), ("+44", "GB"), ("+32", "BE"),
    ("+41", "CH"), ("+352", "LU"), ("+34", "ES"), ("+49", "DE"),
    ("+39", "IT"), ("+31", "NL"), ("+353", "IE"), ("+351", "PT"),
    ("+46", "SE"), ("+47", "NO"), ("+45", "DK"), ("+358", "FI"),
    ("+43", "AT"), ("+48", "PL"), ("+420", "CZ"), ("+36", "HU"),
    ("+40", "RO"), ("+30", "GR"), ("+91", "IN"), ("+81", "JP"),
    ("+86", "CN"), ("+82", "KR"), ("+61", "AU"), ("+64", "NZ"),
    ("+55", "BR"), ("+52", "MX"), ("+7", "RU"),
    ("+971", "AE"), ("+966", "SA"), ("+965", "KW"), ("+972", "IL"),
    ("+212", "MA"), ("+216", "TN"), ("+213", "DZ"),
    ("+221", "SN"), ("+225", "CI"), ("+237", "CM"),
]

def country_from_phone(phone_str):
    """Derive ISO country code from the first phone number's prefix."""
    phone = (phone_str or '').strip()
    if not phone:
        return ''
    # Sort by longest prefix first to avoid +3 matching before +33
    for prefix, code in sorted(PHONE_PREFIX_TO_COUNTRY, key=lambda x: -len(x[0])):
        if phone.startswith(prefix):
            return code
    return ''


def extract_phones_by_prefix(phone_str, prefix):
    """From a '; '-separated list of phones, return only those matching prefix."""
    if not phone_str:
        return ''
    phones = [p.strip() for p in phone_str.split(';') if p.strip()]
    pattern = re.compile(re.escape(prefix) + r'\b')
    matched = [p for p in phones if pattern.search(p)]
    return '; '.join(matched)


def li_slug(url):
    """Extract LinkedIn company slug from URL."""
    url = (url or '').strip().lower().rstrip('/')
    m = re.search(r'/company/([^/?]+)', url)
    return m.group(1) if m else None


def norm(s):
    return (s or '').strip().lower()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 step6_final_output.py <project_name> "
              "--input <file> [--companies <file>] [--phone-prefix +33]")
        sys.exit(1)

    project_name = sys.argv[1]
    input_path = None
    companies_path = None
    phone_prefix = None

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '--input' and i + 1 < len(args):
            input_path = args[i + 1]
            i += 2
        elif args[i] == '--companies' and i + 1 < len(args):
            companies_path = args[i + 1]
            i += 2
        elif args[i] == '--phone-prefix' and i + 1 < len(args):
            phone_prefix = args[i + 1]
            i += 2
        else:
            i += 1

    if not input_path:
        print("Error: --input is required.")
        sys.exit(1)

    output_path = os.path.join(os.path.dirname(os.path.abspath(input_path)),
                               f"{project_name}_FINAL.csv")

    # ── Load step5 contacts ──────────────────────────────────────────────────
    with open(input_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        original_fields = reader.fieldnames
        rows = list(reader)

    print("=" * 60)
    print(f"Step 6 — Final Output: {project_name}")
    print("=" * 60)
    print(f"  Contacts loaded: {len(rows)}")

    # ── Backfill Company Website from step2 companies ────────────────────────
    if companies_path and os.path.isfile(companies_path):
        with open(companies_path, newline='', encoding='utf-8-sig') as f:
            companies = list(csv.DictReader(f))

        by_slug = {}
        by_name = {}
        tech_by_slug = {}
        tech_by_name = {}
        for r in companies:
            domain = (r.get('domain') or r.get('Company Website') or '').strip()
            domain = re.sub(r'^https?://', '', domain).strip('/')
            tech = (r.get('technology_matched') or r.get('technology_slugs_matched') or '').strip()
            if not domain:
                continue
            slug = li_slug(r.get('linkedin_company_url') or r.get('Company Linkedin Flagship Url') or '')
            if slug:
                by_slug[slug] = domain
                if tech:
                    tech_by_slug[slug] = tech
            name_key = norm(r.get('company_name') or r.get('Company Name') or '')
            if name_key:
                by_name[name_key] = domain
                if tech:
                    tech_by_name[name_key] = tech

        filled = 0
        for row in rows:
            if (row.get('Company Website') or '').strip():
                continue
            slug = li_slug(row.get('Company Linkedin Flagship Url') or '')
            name = norm(row.get('Company Name') or '')
            domain = by_slug.get(slug) or by_name.get(name)
            if domain:
                row['Company Website'] = domain
                filled += 1

        still_missing = sum(1 for r in rows if not (r.get('Company Website') or '').strip())
        print(f"\n  Website backfill from companies file:")
        print(f"    Filled: {filled}")
        print(f"    Still missing: {still_missing}")

        # Backfill technology_matched and NAICS
        tech_filled = 0
        naics_by_slug = {}
        naics_by_name = {}
        for r in companies:
            naics = (r.get('naics_code') or '').strip()
            if not naics:
                continue
            slug = li_slug(r.get('linkedin_company_url') or r.get('Company Linkedin Flagship Url') or '')
            if slug:
                naics_by_slug[slug] = naics
            nk = norm(r.get('company_name') or r.get('Company Name') or '')
            if nk:
                naics_by_name[nk] = naics

        naics_filled = 0
        for row in rows:
            slug = li_slug(row.get('Company Linkedin Flagship Url') or '')
            name = norm(row.get('Company Name') or '')
            tech = tech_by_slug.get(slug) or tech_by_name.get(name) or ''
            if tech:
                row['Technology Matched'] = tech
                tech_filled += 1
            naics = naics_by_slug.get(slug) or naics_by_name.get(name) or ''
            if naics:
                row['NAICS Code'] = naics
                naics_filled += 1
        print(f"\n  Technology backfill from companies file:")
        print(f"    Filled: {tech_filled}")
        print(f"  NAICS backfill from companies file:")
        print(f"    Filled: {naics_filled}")

    # ── Phone prefix filter ──────────────────────────────────────────────────
    if phone_prefix:
        phone_cleared = 0
        for row in rows:
            original = (row.get("Phone (Pronto)") or "").strip()
            filtered = extract_phones_by_prefix(original, phone_prefix)
            if original and not filtered:
                phone_cleared += 1
            row["Phone (Pronto)"] = filtered

        print(f"\n  Phone filter (prefix {phone_prefix}):")
        print(f"    Non-matching phones cleared: {phone_cleared}")

    # ── Derive country from phone prefix ──────────────────────────────────────
    country_counts = Counter()
    for row in rows:
        phone = (row.get("Phone (Pronto)") or "").strip()
        cc = country_from_phone(phone)
        row["Contact Country"] = cc
        row["Company HQ Country"] = cc  # best guess from contact phone
        country_counts[cc or "Unknown"] += 1

    print(f"\n  Country derived from phone prefix:")
    for cc, cnt in country_counts.most_common():
        print(f"    {cc}: {cnt}")

    # ── Stats ────────────────────────────────────────────────────────────────
    with_email = sum(1 for r in rows if (r.get("Email") or "").strip())
    with_phone = sum(1 for r in rows if (r.get("Phone (Pronto)") or "").strip())
    with_both = sum(1 for r in rows if (r.get("Email") or "").strip() and (r.get("Phone (Pronto)") or "").strip())
    with_nothing = sum(1 for r in rows if not (r.get("Email") or "").strip() and not (r.get("Phone (Pronto)") or "").strip())

    dist = Counter(r.get("_priority_category", "?") for r in rows)

    print(f"\n  Final output ({len(rows)} contacts):")
    print(f"    With email:  {with_email}")
    print(f"    With phone:  {with_phone}")
    print(f"    With both:   {with_both}")
    print(f"    With nothing: {with_nothing}")
    print(f"\n  Priority distribution:")
    for cat, cnt in sorted(dist.items()):
        pct = cnt / len(rows) * 100 if rows else 0
        print(f"    {cat:<25} {cnt:>3}  ({pct:.0f}%)")
    print(f"    {'TOTAL':<25} {len(rows):>3}")

    # ── Write output ─────────────────────────────────────────────────────────
    # Include original fields + dynamically added fields (Contact Country, Company HQ Country, Technology Matched)
    all_row_keys = set()
    for row in rows:
        all_row_keys.update(row.keys())
    fields_to_write = [f for f in OUTPUT_FIELDS if f in all_row_keys]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields_to_write, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  Output: {len(rows)} contacts -> {output_path}")

    # ── Uncovered companies (no contacts in FINAL) ─────────────────────────
    if companies_path and os.path.isfile(companies_path):
        # Collect all company domains/slugs that appear in FINAL contacts
        covered_slugs = set()
        covered_domains = set()
        for row in rows:
            slug = li_slug(row.get('Company Linkedin Flagship Url') or '')
            if slug:
                covered_slugs.add(slug)
            website = norm(row.get('Company Website') or '')
            if website:
                covered_domains.add(re.sub(r'^https?://', '', website).strip('/').lower())

        uncovered = []
        for r in companies:
            domain = (r.get('domain') or r.get('Company Website') or '').strip()
            domain_clean = re.sub(r'^https?://', '', domain).strip('/').lower()
            slug = li_slug(r.get('linkedin_company_url') or r.get('Company Linkedin Flagship Url') or '')
            if slug in covered_slugs or domain_clean in covered_domains:
                continue
            uncovered.append(r)

        uncovered_path = os.path.join(os.path.dirname(os.path.abspath(input_path)),
                                       f"{project_name}_uncovered_companies.csv")
        if uncovered:
            unc_fields = list(uncovered[0].keys())
            with open(uncovered_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=unc_fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(uncovered)

        total_companies = len(companies)
        covered_count = total_companies - len(uncovered)
        print(f"\n  Company coverage:")
        print(f"    Total companies (step2): {total_companies}")
        print(f"    Covered (≥1 contact):    {covered_count}")
        print(f"    Uncovered (0 contacts):  {len(uncovered)}")
        if uncovered:
            print(f"    -> {uncovered_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
