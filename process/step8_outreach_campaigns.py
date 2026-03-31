#!/usr/bin/env python3
"""
step8_outreach_campaigns.py — Create outreach campaigns and push leads via API
================================================================================
Input:  {project_name}_FINAL.csv
Output: Campaigns created via outreach API (1 per sales rep, paused)

Logic:
  1. Load FINAL.csv
  2. Assign contacts to sales reps via round robin (francophone vs ROW pools)
  3. Group contacts by assigned sales rep
  4. For each rep, create a campaign via API, pause it, then add leads
  5. Push ALL available variables as lead data for AI column personalization

Usage:
    python3 step8_outreach_campaigns.py <project_name> \\
        --input <FINAL.csv>
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time


# ── Config ────────────────────────────────────────────────────────────────────
OUTREACH_API_KEY_ENV = "OUTREACH_API_KEY"
OUTREACH_BASE = "https://api.your-outreach-tool.com/api"

FR_POOL = ['sales1@example.com', 'sales2@example.com']
ROW_POOL = ['sales3@example.com', 'sales4@example.com', 'sales5@example.com']

FRANCOPHONE_COUNTRIES = {
    'FR', 'BE', 'CH', 'LU', 'MA', 'TN', 'DZ', 'SN', 'CI', 'CM',
    'CD', 'MG', 'BF', 'ML', 'NE', 'RW', 'HT',
}

RATE_LIMIT_SLEEP = 0.1

# ── Country detection ─────────────────────────────────────────────────────────
COUNTRY_MAP = {
    'france': 'FR', 'united states': 'US', 'united kingdom': 'GB',
    'uk': 'GB', 'germany': 'DE', 'netherlands': 'NL', 'canada': 'CA',
    'belgium': 'BE', 'switzerland': 'CH', 'luxembourg': 'LU',
    'morocco': 'MA', 'spain': 'ES', 'italy': 'IT', 'portugal': 'PT',
    'sweden': 'SE', 'denmark': 'DK', 'finland': 'FI', 'norway': 'NO',
    'poland': 'PL', 'austria': 'AT', 'israel': 'IL', 'australia': 'AU',
    'india': 'IN', 'japan': 'JP', 'brazil': 'BR', 'mexico': 'MX',
    'argentina': 'AR', 'chile': 'CL', 'colombia': 'CO',
    'united arab emirates': 'AE', 'uae': 'AE',
    'tunisia': 'TN', 'algeria': 'DZ', 'senegal': 'SN',
    'ivory coast': 'CI', 'cameroon': 'CM', 'singapore': 'SG',
    'hong kong': 'HK', 'south africa': 'ZA', 'nigeria': 'NG',
    'kenya': 'KE', 'egypt': 'EG', 'turkey': 'TR', 'russia': 'RU',
    'ukraine': 'UA', 'romania': 'RO', 'czech republic': 'CZ',
    'ireland': 'IE', 'new zealand': 'NZ', 'taiwan': 'TW',
    'south korea': 'KR', 'thailand': 'TH', 'indonesia': 'ID',
}

# Metro area hints: city substrings that map to a country code
METRO_AREAS = {
    'paris': 'FR', 'lyon': 'FR', 'marseille': 'FR', 'toulouse': 'FR',
    'bordeaux': 'FR', 'lille': 'FR', 'nantes': 'FR', 'strasbourg': 'FR',
    'montpellier': 'FR', 'nice': 'FR', 'rennes': 'FR', 'grenoble': 'FR',
    'île-de-france': 'FR', 'ile-de-france': 'FR',
    'bruxelles': 'BE', 'brussels': 'BE', 'antwerp': 'BE', 'ghent': 'BE',
    'genève': 'CH', 'geneva': 'CH', 'zürich': 'CH', 'zurich': 'CH',
    'lausanne': 'CH', 'bern': 'CH', 'basel': 'CH',
    'casablanca': 'MA', 'rabat': 'MA',
    'tunis': 'TN',
    'dakar': 'SN',
    'abidjan': 'CI',
    'london': 'GB', 'manchester': 'GB', 'edinburgh': 'GB', 'birmingham': 'GB',
    'new york': 'US', 'san francisco': 'US', 'los angeles': 'US',
    'chicago': 'US', 'boston': 'US', 'austin': 'US', 'seattle': 'US',
    'denver': 'US', 'atlanta': 'US', 'miami': 'US',
    'berlin': 'DE', 'munich': 'DE', 'hamburg': 'DE', 'frankfurt': 'DE',
    'amsterdam': 'NL', 'rotterdam': 'NL',
    'dublin': 'IE', 'tel aviv': 'IL', 'singapore': 'SG',
}


def parse_location(loc_str):
    """Parse 'City, Region, Country' -> country code."""
    if not loc_str or not loc_str.strip():
        return ''
    lower = loc_str.lower().strip()

    # Direct country name match (last comma-separated part)
    parts = [p.strip() for p in loc_str.split(',')]
    country_name = parts[-1].lower().strip()
    cc = COUNTRY_MAP.get(country_name, '')
    if cc:
        return cc

    # 2-letter country code at end
    if len(country_name) == 2 and country_name.upper().isalpha():
        return country_name.upper()

    # Metro area detection
    for metro, metro_cc in METRO_AREAS.items():
        if metro in lower:
            return metro_cc

    return ''


def get_contact_country(row):
    """Detect country code from contact/company location fields."""
    # Try Company HQ Country first (direct 2-letter code)
    cc = (row.get('Company HQ Country', '') or '').strip().upper()
    if cc and len(cc) == 2:
        return cc

    # Try contact Location field
    loc = (row.get('Location', '') or '').strip()
    if loc:
        cc = parse_location(loc)
        if cc:
            return cc

    # Try Company HQ City with metro area detection
    city = (row.get('Company HQ City', '') or '').strip().lower()
    if city:
        for metro, metro_cc in METRO_AREAS.items():
            if metro in city:
                return metro_cc

    return ''


def col(row, *keys):
    """Return first non-empty value from a list of possible column names."""
    for k in keys:
        v = row.get(k, '').strip()
        if v:
            return v
    return ''


def load_csv(path):
    """Load CSV, skip empty rows."""
    with open(path, newline='', encoding='utf-8-sig') as f:
        return [row for row in csv.DictReader(f) if any(v.strip() for v in row.values())]


def api_call(method, endpoint, api_key, data=None):
    """
    Make an API call using curl subprocess (Cloudflare blocks Python urllib).
    Returns (success: bool, response_data: dict or str).
    """
    url = f"{OUTREACH_BASE}{endpoint}"

    # Basic auth: empty username, API key as password
    # curl format: -u ":password"
    cmd = [
        'curl', '-s', '-w', '\n%{http_code}',
        '-X', method,
        '-u', f':{api_key}',
        '-H', 'Content-Type: application/json',
        url,
    ]

    if data is not None:
        cmd.extend(['-d', json.dumps(data)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout.strip()

        # Split response body and status code
        lines = output.rsplit('\n', 1)
        if len(lines) == 2:
            body, status_code = lines
        else:
            body = output
            status_code = '0'

        try:
            status = int(status_code)
        except ValueError:
            status = 0

        if 200 <= status < 300:
            try:
                return True, json.loads(body) if body else {}
            except json.JSONDecodeError:
                return True, body
        else:
            return False, f"HTTP {status}: {body[:300]}"

    except subprocess.TimeoutExpired:
        return False, "Request timed out"
    except Exception as e:
        return False, str(e)


def build_lead_variables(row):
    """Build the variables dict to push with each lead."""
    variables = {}

    # Standard fields
    mapping = {
        'firstName': ('First Name',),
        'lastName': ('Last Name',),
        'phone': ('Phone (Pronto)',),
        'companyName': ('Company Name',),
        'jobTitle': ('Title',),
        'linkedinUrl': ('Linkedin Profile Url',),
        'location': ('Location',),
        # Company fields
        'companyDescription': ('Company Description',),
        'companyWebsite': ('Company Website',),
        'companyIndustry': ('Company Industry',),
        'companyLinkedinUrl': ('Company Linkedin Flagship Url',),
        'employeeRange': ('Employee Range',),
        # Pricing fields
        'pricingModel': ('pricing_model',),
        'pricingPageUrl': ('pricing_page_url',),
        'hasFreeTier': ('has_free_tier',),
        'hasFreeTrial': ('has_free_trial',),
        'pricingPlans': ('pricing_plans',),
        # Stack fields
        'billingTool': ('billing_tool',),
        'paymentProvider': ('payment_provider',),
        'crm': ('crm',),
        # Context
        'competitorOf': ('competitor_of',),
    }

    for var_name, col_keys in mapping.items():
        value = col(row, *col_keys)
        if value:
            variables[var_name] = value

    return variables


def get_rep_first_name(email):
    """Extract first name from sales rep email. E.g. sales1@example.com -> Sales1."""
    local = email.split('@')[0]
    # Handle firstname.lastname format
    name = local.split('.')[0]
    return name.capitalize()


def main():
    parser = argparse.ArgumentParser(
        description="Create outreach campaigns (one per sales rep) and push leads via API."
    )
    parser.add_argument("project_name",
                        help="Project name (used for campaign naming)")
    parser.add_argument("--input", required=True, dest="input_file",
                        help="Path to FINAL.csv")
    args = parser.parse_args()

    # ── Resolve paths ─────────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)

    input_path = args.input_file
    if not os.path.isabs(input_path):
        input_path = os.path.join(repo_root, input_path)

    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}")
        sys.exit(1)

    # ── Load API key ──────────────────────────────────────────────────────────
    api_key = os.environ.get(OUTREACH_API_KEY_ENV, '')
    if not api_key:
        print(f"ERROR: {OUTREACH_API_KEY_ENV} environment variable not set.")
        sys.exit(1)

    # ── Load contacts ─────────────────────────────────────────────────────────
    contacts = load_csv(input_path)

    print(f"\n{'='*60}")
    print(f"Step 8: Outreach Campaign Creation")
    print(f"{'='*60}")
    print(f"Input: {input_path} ({len(contacts)} contacts)")

    # ── Assign contacts to sales reps via round robin ─────────────────────────
    # Group companies by domain first, assign owner per company (not per contact)
    company_owners = {}  # domain -> rep email
    fr_counter = 0
    row_counter = 0

    # First pass: assign owners to companies
    for row in contacts:
        domain = col(row, 'Company Website').lower()
        domain = re.sub(r'^https?://', '', domain).strip('/')
        if not domain or domain in company_owners:
            continue

        cc = get_contact_country(row)
        if cc in FRANCOPHONE_COUNTRIES:
            company_owners[domain] = FR_POOL[fr_counter % len(FR_POOL)]
            fr_counter += 1
        else:
            company_owners[domain] = ROW_POOL[row_counter % len(ROW_POOL)]
            row_counter += 1

    # Second pass: assign rep to each contact based on company
    rep_contacts = {}  # rep email -> list of rows
    unassigned = 0

    for row in contacts:
        email = col(row, 'Email')
        if not email:
            unassigned += 1
            continue

        domain = col(row, 'Company Website').lower()
        domain = re.sub(r'^https?://', '', domain).strip('/')
        rep = company_owners.get(domain, '')

        if not rep:
            # Fallback: assign directly
            cc = get_contact_country(row)
            if cc in FRANCOPHONE_COUNTRIES:
                rep = FR_POOL[fr_counter % len(FR_POOL)]
                fr_counter += 1
            else:
                rep = ROW_POOL[row_counter % len(ROW_POOL)]
                row_counter += 1

        rep_contacts.setdefault(rep, []).append(row)

    # ── Summary before API calls ──────────────────────────────────────────────
    fr_total = sum(len(leads) for rep, leads in rep_contacts.items() if rep in FR_POOL)
    row_total = sum(len(leads) for rep, leads in rep_contacts.items() if rep in ROW_POOL)

    print(f"\nRound robin assignment:")
    print(f"  Francophone pool: {fr_total} contacts")
    print(f"  ROW pool:         {row_total} contacts")
    if unassigned:
        print(f"  Unassigned (no email): {unassigned}")

    print(f"\nCampaigns to create:")
    for rep, leads in sorted(rep_contacts.items()):
        print(f"  {get_rep_first_name(rep)} ({rep}): {len(leads)} leads")

    # ── Build project descriptor for campaign name ────────────────────────────
    # Convert project_name like "fr_tech500_2026" to "FR Tech500 2026"
    project_label = args.project_name.replace('_', ' ').title()

    # ── Create campaigns and push leads ───────────────────────────────────────
    print(f"\nCreating campaigns...")

    campaign_results = []

    for rep_email, leads in sorted(rep_contacts.items()):
        rep_name = get_rep_first_name(rep_email)
        campaign_name = f"{project_label} - {rep_name}"

        # Create campaign
        print(f"\n  Creating campaign: {campaign_name}")
        ok, resp = api_call('POST', '/campaigns', api_key, {'name': campaign_name})

        if not ok:
            print(f"    ERROR creating campaign: {resp}")
            campaign_results.append({
                'name': campaign_name,
                'id': None,
                'leads_added': 0,
                'errors': 1,
                'error_detail': str(resp),
            })
            continue

        campaign_id = resp.get('_id', '') if isinstance(resp, dict) else ''
        if not campaign_id:
            print(f"    ERROR: no campaign ID in response: {resp}")
            campaign_results.append({
                'name': campaign_name,
                'id': None,
                'leads_added': 0,
                'errors': 1,
                'error_detail': 'No campaign ID returned',
            })
            continue

        print(f"    Campaign ID: {campaign_id}")

        # Pause campaign immediately
        ok_pause, _ = api_call('POST', f'/campaigns/{campaign_id}/pause', api_key)
        if ok_pause:
            print(f"    Campaign paused")
        else:
            print(f"    WARNING: failed to pause campaign")

        # Add leads
        leads_added = 0
        errors = 0

        for row in leads:
            lead_email = col(row, 'Email')
            if not lead_email:
                errors += 1
                continue

            variables = build_lead_variables(row)

            ok_lead, lead_resp = api_call(
                'POST',
                f'/campaigns/{campaign_id}/leads/{lead_email}',
                api_key,
                variables,
            )

            if ok_lead:
                leads_added += 1
            else:
                errors += 1
                if errors <= 3:
                    print(f"    ERROR adding {lead_email}: {lead_resp}")

            time.sleep(RATE_LIMIT_SLEEP)

        print(f"    Leads added: {leads_added}, Errors: {errors}")

        campaign_results.append({
            'name': campaign_name,
            'id': campaign_id,
            'leads_added': leads_added,
            'errors': errors,
        })

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Step 8: Outreach Campaign Creation — Summary")
    print(f"{'='*60}")
    print(f"Total contacts:   {len(contacts)}")
    print(f"Campaigns created: {len(campaign_results)}")
    print()

    total_leads = 0
    total_errors = 0
    for result in campaign_results:
        status = f"ID: {result['id']}" if result['id'] else "FAILED"
        print(f"  {result['name']}")
        print(f"    {status} | Leads: {result['leads_added']} | Errors: {result['errors']}")
        total_leads += result['leads_added']
        total_errors += result['errors']

    print(f"\n  Total leads pushed: {total_leads}")
    print(f"  Total errors:       {total_errors}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
