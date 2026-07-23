#!/usr/bin/env python3
"""
step1f_validate_pricing_pages.py — Validate detected pricing pages
===================================================================
Adapted from pricing-benchmark-gpt/scripts/validate_pricing_pages.py
for CSV-based list-building pipeline.

Fetches each detected pricing page and checks for real pricing signals:
- Currency symbols ($, EUR, £)
- Pricing terms (per month, per user, /mo, /yr, annually, monthly)
- Plan names (free, starter, pro, enterprise, basic, premium, business)
- Price patterns (digits followed by currency or period)

Usage:
    python3 step1f_validate_pricing_pages.py <project_name> \
        --input <step1e_pricing_detected.csv>

Output:
    {project_name}_step1f_pricing_validated.csv
        Same columns as input + valid_pricing_page, pricing_score, pricing_description
"""

import argparse
import asyncio
import csv
import os
import re
import sys

import aiohttp

TIMEOUT = 8
CONCURRENT = 15

# --- Pricing signals ---

CURRENCY_PATTERNS = re.compile(r'[\$€£]\s?\d+|\d+\s?[\$€£]|USD|EUR|GBP')

PRICING_TERMS = re.compile(
    r'/mo(?:nth)?|/yr|/year|per\s+month|per\s+user|per\s+seat|per\s+agent'
    r'|annually|monthly|yearly|billed\s+annually|billed\s+monthly'
    r'|free\s+trial|free\s+plan|free\s+tier|start(?:ing)?\s+at'
    r'|most\s+popular|best\s+value|recommended'
    r'|custom\s+pricing|contact\s+(?:us|sales)|get\s+a\s+quote'
    r'|tarif|mois|an(?:nuel)?|utilisateur|gratuit',
    re.IGNORECASE,
)

PLAN_NAMES = re.compile(
    r'\b(?:free|starter|basic|essentials?|standard|professional|pro|plus'
    r'|premium|business|enterprise|team|growth|scale|unlimited)\b',
    re.IGNORECASE,
)

PRICE_PATTERN = re.compile(r'\d{1,6}[.,]\d{2}')


def strip_html(html):
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&#?\w+;', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def analyze_pricing_page(html):
    text = strip_html(html)

    if len(text) < 100:
        return False, 0, None

    score = 0
    signals = []

    currency_matches = CURRENCY_PATTERNS.findall(text)
    if currency_matches:
        score += min(len(currency_matches), 5) * 2
        signals.append(f"{len(currency_matches)} price mentions")

    term_matches = set(PRICING_TERMS.findall(text))
    if term_matches:
        score += min(len(term_matches), 5) * 3
        signals.append(f"terms: {', '.join(list(term_matches)[:3])}")

    plan_matches = set(m.lower() for m in PLAN_NAMES.findall(text))
    if plan_matches:
        score += min(len(plan_matches), 5) * 2
        signals.append(f"plans: {', '.join(sorted(plan_matches)[:5])}")

    price_matches = PRICE_PATTERN.findall(text)
    if price_matches:
        score += min(len(price_matches), 5)
        signals.append(f"{len(price_matches)} price values")

    is_valid = score >= 5
    description = "; ".join(signals) if is_valid and signals else None

    return is_valid, score, description


async def fetch_and_validate(session, url):
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                return False, 0, None
            html = await resp.text(errors="ignore")
            return analyze_pricing_page(html)
    except Exception:
        return False, 0, None


async def run_validation(pages):
    semaphore = asyncio.Semaphore(CONCURRENT)
    results = {}
    done = 0
    valid_count = 0
    total = len(pages)

    async def validate_with_limit(domain, url):
        nonlocal done, valid_count
        async with semaphore:
            is_valid, score, description = await fetch_and_validate(session, url)
            results[domain] = (is_valid, score, description)
            done += 1
            if is_valid:
                valid_count += 1
            if done % 50 == 0 or done == total:
                print(f"  Validated {done}/{total} — {valid_count} confirmed", flush=True)

    async with aiohttp.ClientSession() as session:
        tasks = [validate_with_limit(d, u) for d, u in pages]
        await asyncio.gather(*tasks)

    return results


def main():
    parser = argparse.ArgumentParser(description="Validate detected pricing pages")
    parser.add_argument("project_name", help="Project name")
    parser.add_argument("--input", required=True, dest="input_file", help="Input CSV (from step1e)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    project_dir = os.path.join(repo_root, args.project_name)

    input_path = args.input_file
    if not os.path.isabs(input_path):
        input_path = os.path.join(repo_root, input_path)

    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}")
        sys.exit(1)

    with open(input_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print(f"Input: {len(rows)} rows")

    # Collect pages to validate (only rows with a detected pricing page)
    pages = []
    for r in rows:
        url = r.get("pricing_page_url", "").strip()
        domain = r.get("domain", "").strip().lower()
        if url and domain:
            pages.append((domain, url))

    print(f"Pricing pages to validate: {len(pages)}")

    if not pages:
        print("No pricing pages detected. Nothing to validate.")
        # Still write output with empty validation columns
        output_columns = list(rows[0].keys())
        for col in ["valid_pricing_page", "pricing_score", "pricing_description"]:
            if col not in output_columns:
                output_columns.append(col)
        for r in rows:
            r["valid_pricing_page"] = ""
            r["pricing_score"] = ""
            r["pricing_description"] = ""

        output_filename = f"{args.project_name}_step1f_pricing_validated.csv"
        output_path = os.path.join(project_dir, output_filename)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"Output: {output_path}")
        return

    print(f"Validating ({CONCURRENT} concurrent)...\n")
    results = asyncio.run(run_validation(pages))

    # Add validation columns to rows
    output_columns = list(rows[0].keys())
    for col in ["valid_pricing_page", "pricing_score", "pricing_description"]:
        if col not in output_columns:
            output_columns.append(col)

    for r in rows:
        domain = r.get("domain", "").strip().lower()
        if domain in results:
            is_valid, score, description = results[domain]
            r["valid_pricing_page"] = "true" if is_valid else "false"
            r["pricing_score"] = str(score)
            r["pricing_description"] = description or ""
        else:
            r["valid_pricing_page"] = ""
            r["pricing_score"] = ""
            r["pricing_description"] = ""

    # Write output
    output_filename = f"{args.project_name}_step1f_pricing_validated.csv"
    output_path = os.path.join(project_dir, output_filename)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Report
    valid = sum(1 for v, s, d in results.values() if v)
    invalid = len(results) - valid

    scores = [s for v, s, d in results.values()]
    brackets = {"0": 0, "1-4": 0, "5-9": 0, "10-19": 0, "20+": 0}
    for s in scores:
        if s == 0:
            brackets["0"] += 1
        elif s < 5:
            brackets["1-4"] += 1
        elif s < 10:
            brackets["5-9"] += 1
        elif s < 20:
            brackets["10-19"] += 1
        else:
            brackets["20+"] += 1

    print(f"\n{'='*60}")
    print(f"Step 1f: Pricing Page Validation")
    print(f"{'='*60}")
    print(f"  Pages checked:      {len(results)}")
    print(f"  Valid pricing:      {valid}")
    print(f"  False positives:    {invalid}")
    print(f"")
    print(f"  Score distribution:")
    for bracket, count in brackets.items():
        print(f"    {bracket}: {count}")
    print(f"")
    print(f"  Output: {output_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
