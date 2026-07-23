#!/usr/bin/env python3
"""
step2c_pronto_resolution_audit.py — Audit Pronto's account-list resolution
==========================================================================
Run AFTER uploading the step2b Pronto import CSV to Pronto AND exporting the
account list from Pronto UI. Compares what we uploaded vs what Pronto resolved.

Surfaces three Pronto-side failure modes BEFORE running the Sales Nav search,
so you can fix slug issues without spending Pronto credits on a wasted search:

  - Pattern 2: empty resolved LinkedIn ID (Pronto couldn't match the upload)
  - Pattern 3: low employee count (resolved to a stale/zombie LinkedIn entity)
  - Country mismatch (e.g. FR company resolved to a Latvian entity)

The downstream zero-leads alarm in step4 still catches misses, but this audit
saves a round-trip: fix problems before they cost credits.

Usage:
    python3 step2c_pronto_resolution_audit.py <project_name> \\
        --uploaded <step2b_pronto_import.csv> \\
        --resolved <pronto_company_export.csv>

Pronto export CSV format (16 columns, duplicated header names):
  0-3   uploaded:   Company Name | Company Website | LinkedIn URL | LinkedIn ID
  4-15  resolved:   Company Name | Website | Domain | Description | Country |
                    Location | Industry | Employee Range | Employee Count |
                    LinkedIn URL | LinkedIn ID Url | LinkedIn ID
"""

import argparse
import csv
import os
import re
import sys


# Low employee count threshold — if Pronto-resolved entity has fewer than this,
# it's very likely a zombie/wrong page (real B2B SaaS targets in the seed list
# typically have 30+ employees). False-positive risk on truly tiny startups
# but those are usually filtered out at step1d / step2 anyway.
LOW_EMPLOYEE_THRESHOLD = 15


def normalize_country(s: str) -> str:
    """Lowercase + strip + map common variants for cross-source comparison."""
    s = (s or '').strip().lower()
    aliases = {
        'united states': 'us', 'usa': 'us', 'us': 'us',
        'united kingdom': 'gb', 'uk': 'gb', 'gb': 'gb',
        'france': 'fr', 'fr': 'fr',
        'germany': 'de', 'de': 'de',
        'belgium': 'be', 'switzerland': 'ch', 'luxembourg': 'lu',
    }
    return aliases.get(s, s)


def load_uploaded(path: str) -> dict:
    """Return uploaded data keyed by Company Name (case-insensitive)."""
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            name = (row.get('Company Name') or '').strip()
            if name:
                out[name.lower()] = row
    return out


def load_resolved(path: str) -> list:
    """Return list of (uploaded_name, resolved_dict) tuples from Pronto export."""
    rows_out = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader)
        if len(header) < 16:
            print(f"ERROR: expected 16-column Pronto export, got {len(header)} columns")
            sys.exit(1)
        for r in reader:
            if len(r) < 16:
                continue
            up_name = r[0].strip()
            resolved = {
                'name': r[4].strip(),
                'website': r[5].strip(),
                'domain': r[6].strip(),
                'description': r[7].strip(),
                'country': r[8].strip(),
                'location': r[9].strip(),
                'industry': r[10].strip(),
                'employee_range': r[11].strip(),
                'employee_count': r[12].strip(),
                'linkedin_url': r[13].strip(),
                'linkedin_id_url': r[14].strip(),
                'linkedin_id': r[15].strip(),
            }
            rows_out.append((up_name, resolved))
    return rows_out


def load_apollo_db(repo_root: str) -> dict:
    """Load Apollo DB by domain for cross-checking employee count / country."""
    path = os.path.join(repo_root, 'database', 'apollo_companies_database.csv')
    if not os.path.isfile(path):
        return {}
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            d = (row.get('domain') or '').strip().lower()
            if d:
                out[d] = row
    return out


def main():
    parser = argparse.ArgumentParser(
        description='Audit Pronto resolution vs the step2b upload.'
    )
    parser.add_argument('project_name')
    parser.add_argument('--uploaded', required=True,
                        help='Path to step2b_pronto_import.csv')
    parser.add_argument('--resolved', required=True,
                        help='Path to Pronto company export CSV')
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)

    if not os.path.isfile(args.uploaded):
        print(f"ERROR: uploaded file not found: {args.uploaded}")
        sys.exit(1)
    if not os.path.isfile(args.resolved):
        print(f"ERROR: resolved file not found: {args.resolved}")
        sys.exit(1)

    uploaded = load_uploaded(args.uploaded)
    resolved = load_resolved(args.resolved)
    apollo = load_apollo_db(repo_root)

    print('=' * 60)
    print(f'Step 2c: Pronto Resolution Audit — {args.project_name}')
    print('=' * 60)
    print(f'Uploaded companies:  {len(uploaded)}')
    print(f'Resolved rows:       {len(resolved)}')
    print()

    # ── Issues ────────────────────────────────────────────────────────────────
    issue_unresolved = []   # empty resolved fields → Pronto couldn't match
    issue_low_emp = []      # resolved emp count is suspiciously low
    issue_country = []      # resolved country diverges from Apollo's
    issue_name = []         # resolved name diverges from uploaded
    in_export_names = set()

    for up_name, rs in resolved:
        in_export_names.add(up_name.lower())
        # Pronto resolution failure signal: all enrichment fields empty.
        # When Pronto can't match a company, it echoes back what we sent (name +
        # LinkedIn URL we provided) but populates none of the data fields it
        # would normally fill (website, employee count, employee range, numeric
        # LinkedIn ID, description). The numeric `linkedin_id` is what Sales Nav
        # actually uses to scope; without it, the company isn't in the search.
        resolution_empty = (
            not rs['website']
            and not rs['employee_range']
            and not rs['employee_count']
            and not rs['linkedin_id']
        )
        if resolution_empty:
            issue_unresolved.append({
                'name': up_name,
                'resolved_name': rs['name'],
            })
            continue

        # Low employee count
        try:
            emp_count = int(rs['employee_count']) if rs['employee_count'] else 0
        except ValueError:
            emp_count = 0
        if 0 < emp_count < LOW_EMPLOYEE_THRESHOLD:
            issue_low_emp.append({
                'name': up_name,
                'resolved_name': rs['name'],
                'employee_count': emp_count,
                'country': rs['country'],
                'resolved_linkedin_id': rs['linkedin_id'],
            })
        elif emp_count == 0 and rs['employee_range']:
            # 0 count but a range present — borderline, flag it
            issue_low_emp.append({
                'name': up_name,
                'resolved_name': rs['name'],
                'employee_count': f"0 ({rs['employee_range']})",
                'country': rs['country'],
                'resolved_linkedin_id': rs['linkedin_id'],
            })

        # Country mismatch against Apollo
        uploaded_row = uploaded.get(up_name.lower(), {})
        domain = (uploaded_row.get('Company Website') or '').strip().lower()
        apollo_row = apollo.get(domain, {})
        apollo_country = apollo_row.get('country', '')
        if apollo_country and rs['country']:
            if normalize_country(apollo_country) != normalize_country(rs['country']):
                issue_country.append({
                    'name': up_name,
                    'apollo_country': apollo_country,
                    'pronto_country': rs['country'],
                })

        # Name mismatch — case/punct-insensitive
        def norm(s):
            return re.sub(r'[^a-z0-9]', '', (s or '').lower())
        if rs['name'] and norm(up_name) != norm(rs['name']):
            issue_name.append({
                'name': up_name,
                'resolved_name': rs['name'],
            })

    # Companies in upload but completely missing from Pronto export (worst case)
    missing_from_export = sorted(set(uploaded.keys()) - in_export_names)

    # ── Report ────────────────────────────────────────────────────────────────
    print(f'PATTERN 2 — Pronto failed to resolve (empty resolved LinkedIn ID): {len(issue_unresolved)}')
    for i in issue_unresolved:
        rn = i.get('resolved_name', '')
        if rn and rn.lower() != i['name'].lower():
            print(f"  - {i['name']} (resolved name shows {rn!r} but no LinkedIn ID)")
        else:
            print(f"  - {i['name']}")
    print()
    print(f'MISSING FROM EXPORT — Pronto silently dropped these from account list: {len(missing_from_export)}')
    for n in missing_from_export:
        print(f"  - {uploaded[n].get('Company Name', n)}")
    print()
    print(f'PATTERN 3 — Resolved to LOW-EMPLOYEE entity (likely wrong/zombie page): {len(issue_low_emp)}')
    for i in issue_low_emp:
        print(f"  - {i['name']:<25} resolved as {i['resolved_name']!r:<25} "
              f"emp={i['employee_count']} country={i['country']} LI={i['resolved_linkedin_id']}")
    print()
    print(f'COUNTRY MISMATCH (Apollo vs Pronto resolved): {len(issue_country)}')
    for i in issue_country:
        print(f"  - {i['name']:<25} apollo={i['apollo_country']!r:<10} pronto={i['pronto_country']!r}")
    print()
    print(f'NAME MISMATCH (resolved name differs from uploaded): {len(issue_name)}')
    for i in issue_name:
        print(f"  - {i['name']:<25} resolved as {i['resolved_name']!r}")
    print()

    total_issues = (len(issue_unresolved) + len(missing_from_export) +
                    len(issue_low_emp) + len(issue_country) + len(issue_name))
    print('=' * 60)
    print(f'SUMMARY: {total_issues} issue(s) flagged across {len(uploaded)} companies')
    print('=' * 60)
    if total_issues:
        print('\nNext steps:')
        print('  1. For each flagged company, WebSearch the real LinkedIn slug + company info')
        print('  2. If different from apollo_companies_database.csv, patch the DB and the step1c verified file')
        print('  3. Re-export the step2b Pronto import CSV (step2b will pick up the fixes)')
        print('  4. Re-upload to Pronto and re-export the account list')
        print('  5. Re-run this audit until clean, THEN run the Sales Nav search')
    else:
        print('\nNo issues found — safe to run the Sales Nav search.')


if __name__ == '__main__':
    main()
