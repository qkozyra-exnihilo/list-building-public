#!/usr/bin/env python3
"""
step1e_detect_pricing_pages.py — Detect pricing pages for companies
====================================================================
Adapted from pricing-benchmark-gpt/scripts/detect_pricing_pages.py
for CSV-based list-building pipeline.

Pipeline (stops at first match per domain):
1. Common URL patterns (/pricing, /plans, /tarifs, etc.)
2. Homepage link scan (search for pricing keywords in <a> tags)
3. Sitemap scan (/sitemap.xml)

Usage:
    python3 step1e_detect_pricing_pages.py <project_name> \
        --input <step1d_icp_filtered.csv> \
        --domain-col domain

Output:
    {project_name}_step1e_pricing_detected.csv
        Same columns as input + pricing_page_url, pricing_detected_by
"""

import argparse
import asyncio
import csv
import os
import re
import sys

import aiohttp

TIMEOUT = 5
CONCURRENT = 20
MIN_CONTENT_LENGTH = 500

PRICING_PATHS = [
    "/pricing", "/plans", "/tarifs", "/prices",
    "/plans-and-pricing", "/pricing-plans",
    "/tarification", "/offres", "/offers",
]

FALSE_POSITIVE_PATTERNS = [
    "plan-du-site", "sitemap", "plan_du_site",
    "cread.php", "awinmid", "awinaffid", "affiliate",
    "chauffeur", "aide-a-domicile", "ingredients",
    "insurance-plans", "standard-charges", "cost-of-",
    "/blog/", "/news/", "/resources/", "/articles/",
    "/webinars/", "/webinar", "/newsletter",
    "/analyses/", "/docs/", "/story/",
    "/corporate/", "/community/", "/diensten/",
    "/catalyst/", "/billing/", "/buying/",
    "subscribe-to-", "speaker-list",
    "/solutions/pricing", "how-to-price",
    "credit-operations", "chaine-approvisionnement",
    "logiciel-plan-",
    ".gouv.",
    ".aspx",
    "apps.apple.com",
    "/subscribe", "/newsletter", "/podcast",
    "youtube.com", "substack.com", "beehiiv.com",
]

VALID_PRICING_ENDINGS = re.compile(
    r'/(pricing|tarifs?|plans?|prices?|offres|offers|tarification)/?$', re.IGNORECASE
)

PRICING_KEYWORDS = re.compile(
    r'pricing|price|plans?[\s"\'/>]|tarifs?|tarification|subscribe|subscription',
    re.IGNORECASE,
)


def is_false_positive_url(url):
    url_lower = url.lower()
    for pattern in FALSE_POSITIVE_PATTERNS:
        if pattern in url_lower:
            return True
    if len(url) > 80 and not VALID_PRICING_ENDINGS.search(url) and "/pricing/" not in url_lower:
        return True
    return False


async def check_url(session, url):
    if is_false_positive_url(url):
        return False, None
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status == 200:
                content = await resp.read()
                if len(content) >= MIN_CONTENT_LENGTH:
                    return True, await resp.text(errors="ignore")
    except Exception:
        pass
    return False, None


async def step1_common_paths(session, domain):
    for path in PRICING_PATHS:
        url = f"https://{domain}{path}"
        valid, _ = await check_url(session, url)
        if valid:
            return url
    return None


async def step2_homepage_links(session, domain):
    try:
        async with session.get(
            f"https://{domain}",
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                return None
            html = await resp.text(errors="ignore")
    except Exception:
        return None

    links = re.findall(
        r'<a[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>',
        html, re.IGNORECASE | re.DOTALL,
    )

    for href, text in links:
        if PRICING_KEYWORDS.search(href) or PRICING_KEYWORDS.search(text):
            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = f"https://{domain}{href}"
            else:
                full_url = f"https://{domain}/{href}"

            valid, _ = await check_url(session, full_url)
            if valid:
                return full_url

    anchors = re.findall(r'href=["\']#([^"\']+)["\']', html)
    for anchor in anchors:
        if PRICING_KEYWORDS.search(anchor):
            return f"https://{domain}/#{anchor}"

    return None


async def step3_sitemap(session, domain):
    try:
        async with session.get(
            f"https://{domain}/sitemap.xml",
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            if resp.status != 200:
                return None
            xml = await resp.text(errors="ignore")
    except Exception:
        return None

    urls = re.findall(r'<loc>([^<]+)</loc>', xml, re.IGNORECASE)
    for url in urls:
        if PRICING_KEYWORDS.search(url):
            valid, _ = await check_url(session, url)
            if valid:
                return url

    return None


async def detect_pricing(session, domain):
    url = await step1_common_paths(session, domain)
    if url:
        return url, "common_path"

    url = await step2_homepage_links(session, domain)
    if url:
        return url, "homepage_link"

    url = await step3_sitemap(session, domain)
    if url:
        return url, "sitemap"

    return None, None


async def run_detection(domains):
    semaphore = asyncio.Semaphore(CONCURRENT)
    results = {}
    done = 0
    found = 0
    total = len(domains)
    step_counts = {"common_path": 0, "homepage_link": 0, "sitemap": 0}

    async def detect_with_limit(domain):
        nonlocal done, found
        async with semaphore:
            url, step = await detect_pricing(session, domain)
            if url:
                results[domain] = (url, step)
                found += 1
                step_counts[step] += 1
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  Checked {done}/{total} — {found} pricing pages found "
                      f"(paths:{step_counts['common_path']} links:{step_counts['homepage_link']} "
                      f"sitemap:{step_counts['sitemap']})", flush=True)

    async with aiohttp.ClientSession() as session:
        tasks = [detect_with_limit(d) for d in domains]
        await asyncio.gather(*tasks)

    return results, step_counts


def main():
    parser = argparse.ArgumentParser(description="Detect pricing pages for companies")
    parser.add_argument("project_name", help="Project name")
    parser.add_argument("--input", required=True, dest="input_file", help="Input CSV")
    parser.add_argument("--domain-col", default="domain", help="Column containing domain")
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

    # Extract unique domains (skip flagged)
    domains = []
    seen = set()
    for r in rows:
        d = r.get(args.domain_col, "").strip().lower()
        if d and d not in seen and r.get("domain_flag", "") != "MISSING_DOMAIN":
            domains.append(d)
            seen.add(d)

    print(f"Unique domains to check: {len(domains)}")
    print(f"Detecting pricing pages ({CONCURRENT} concurrent)...\n")

    results, step_counts = asyncio.run(run_detection(domains))

    # Build domain -> result lookup
    domain_results = {}
    for d, (url, step) in results.items():
        domain_results[d] = {"url": url, "step": step}

    # Add columns to rows
    output_columns = list(rows[0].keys())
    for col in ["pricing_page_url", "pricing_detected_by"]:
        if col not in output_columns:
            output_columns.append(col)

    for r in rows:
        d = r.get(args.domain_col, "").strip().lower()
        if d in domain_results:
            r["pricing_page_url"] = domain_results[d]["url"]
            r["pricing_detected_by"] = domain_results[d]["step"]
        else:
            r["pricing_page_url"] = ""
            r["pricing_detected_by"] = ""

    # Write output
    output_filename = f"{args.project_name}_step1e_pricing_detected.csv"
    output_path = os.path.join(project_dir, output_filename)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Report
    with_pricing = sum(1 for r in rows if r.get("pricing_page_url"))
    without_pricing = len(rows) - with_pricing

    print(f"\n{'='*60}")
    print(f"Step 1e: Pricing Page Detection")
    print(f"{'='*60}")
    print(f"  Total companies:    {len(rows)}")
    print(f"  Pricing page found: {with_pricing}")
    print(f"  No pricing page:    {without_pricing}")
    print(f"")
    print(f"  By method:")
    print(f"    Common URL paths: {step_counts['common_path']}")
    print(f"    Homepage links:   {step_counts['homepage_link']}")
    print(f"    Sitemap:          {step_counts['sitemap']}")
    print(f"")
    print(f"  Output: {output_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
