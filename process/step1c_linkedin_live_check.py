#!/usr/bin/env python3
"""
step1c_linkedin_live_check.py — Live LinkedIn slug validation
==============================================================
Hits each company's linkedin_company_url via HTTP HEAD to catch slug issues
the cached Apollo data can't tell you about:

  - 404 → slug is dead on LinkedIn (Apollo cached a bogus slug — e.g. Foederis)
  - 200 with redirect to a different slug → rebrand happened (e.g. Platform.sh
    redirects to upsundotcom). The real slug is the redirect target.
  - 200 same slug → passes (may still be a stale-but-alive zombie like Skeepers
    s-keeper; caught at step2c by employee-count signal instead).
  - other (5xx, timeout, 999) → inconclusive, retry or skip

Outputs a verification CSV in the same format as step1c_verification.csv so
results can be applied via step1c_domain_verification.py --corrections.

Usage:
    python3 step1c_linkedin_live_check.py <project_name> \\
        --input <step1c_or_step2_companies.csv> \\
        [--workers 5] [--rate-limit 1.0]

LinkedIn rate-limits aggressively. Default workers=5 + 1s/req keeps us under
the threshold most of the time. Bump --workers up at your own risk.
"""

import argparse
import csv
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock


USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


def extract_slug(url: str) -> str:
    """Pull /company/<slug> from a LinkedIn URL."""
    if not url:
        return ''
    m = re.search(r'/company/([^/?#]+)', url.lower())
    return m.group(1) if m else ''


def head_check(slug: str, timeout: int = 15) -> dict:
    """Perform HEAD request on LinkedIn company URL. Returns status + final URL.

    Status values:
      - 'ok'           → 200, slug resolves to itself
      - 'redirect'     → 200 but final URL has a different slug (rebrand)
      - 'not_found'    → 404, slug is dead
      - 'rate_limited' → 999 or 429, can't tell
      - 'error'        → network/timeout/other
    """
    url = f'https://www.linkedin.com/company/{slug}/'
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT}, method='HEAD')
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        final_slug = extract_slug(resp.url)
        if resp.status == 200:
            if final_slug and final_slug != slug:
                return {'status': 'redirect', 'http_code': 200,
                        'final_slug': final_slug, 'final_url': resp.url}
            return {'status': 'ok', 'http_code': 200,
                    'final_slug': slug, 'final_url': resp.url}
        return {'status': 'error', 'http_code': resp.status, 'final_slug': '', 'final_url': resp.url}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'status': 'not_found', 'http_code': 404, 'final_slug': '', 'final_url': url}
        if e.code in (429, 999):
            return {'status': 'rate_limited', 'http_code': e.code, 'final_slug': '', 'final_url': url}
        return {'status': 'error', 'http_code': e.code, 'final_slug': '', 'final_url': url}
    except Exception as e:
        return {'status': 'error', 'http_code': 0, 'final_slug': '',
                'final_url': url, 'error': str(e)}


def main():
    parser = argparse.ArgumentParser(
        description='Live HTTP HEAD check on LinkedIn company URLs.'
    )
    parser.add_argument('project_name')
    parser.add_argument('--input', required=True, dest='input_file',
                        help='CSV with company_name + linkedin_company_url columns')
    parser.add_argument('--workers', type=int, default=5,
                        help='Parallel HEAD workers (default 5; LinkedIn rate-limits hard)')
    parser.add_argument('--rate-limit', type=float, default=1.0, dest='rate_limit',
                        help='Min seconds between requests per worker (default 1.0)')
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    project_dir = os.path.join(repo_root, args.project_name)
    if not os.path.isdir(project_dir):
        os.makedirs(project_dir, exist_ok=True)

    input_path = args.input_file
    if not os.path.isabs(input_path):
        input_path = os.path.join(repo_root, input_path)
    if not os.path.isfile(input_path):
        print(f'ERROR: input not found: {input_path}')
        sys.exit(1)

    with open(input_path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    print('=' * 60)
    print(f'Step 1c Live: LinkedIn URL Check — {args.project_name}')
    print('=' * 60)
    print(f'Input:    {input_path}')
    print(f'Rows:     {len(rows)}')
    print(f'Workers:  {args.workers}  (rate-limit {args.rate_limit}s/req)')
    print()

    # Build a list of (row_index, slug, company_name) — skip rows without a URL
    work = []
    for i, row in enumerate(rows):
        url = (row.get('linkedin_company_url') or
               row.get('LinkedIn URL') or
               row.get('linkedin_url') or '').strip()
        slug = extract_slug(url)
        if slug:
            name = (row.get('company_name') or row.get('Company Name') or '').strip()
            work.append((i, slug, name, url))

    print(f'Slugs to check: {len(work)}')
    print()

    last_request_time = [0.0]  # mutable so it's writable in the closure
    rate_lock = Lock()
    results = {}

    def run_one(item):
        idx, slug, name, url = item
        with rate_lock:
            elapsed = time.time() - last_request_time[0]
            if elapsed < args.rate_limit:
                time.sleep(args.rate_limit - elapsed)
            last_request_time[0] = time.time()
        return idx, slug, name, url, head_check(slug)

    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(run_one, w) for w in work]
        for fut in as_completed(futures):
            idx, slug, name, url, res = fut.result()
            results[idx] = (slug, name, url, res)
            completed += 1
            status_icon = {
                'ok': '✓', 'redirect': '↪', 'not_found': '✗',
                'rate_limited': '?', 'error': '!',
            }.get(res['status'], '?')
            print(f'  [{completed:>3}/{len(work)}] {status_icon} {name:<25} slug={slug:<25} '
                  f'-> {res["status"]}'
                  + (f' (real: {res["final_slug"]})' if res['status'] == 'redirect' else ''))

    print()

    # ── Categorize ────────────────────────────────────────────────────────────
    not_found = [r for r in results.values() if r[3]['status'] == 'not_found']
    redirected = [r for r in results.values() if r[3]['status'] == 'redirect']
    rate_limited = [r for r in results.values() if r[3]['status'] == 'rate_limited']
    errored = [r for r in results.values() if r[3]['status'] == 'error']

    print('=' * 60)
    print('SUMMARY')
    print('=' * 60)
    print(f'  OK (slug resolves):      {sum(1 for r in results.values() if r[3]["status"] == "ok")}')
    print(f'  Redirected (rebrand):    {len(redirected)}')
    print(f'  Not found (404):         {len(not_found)}')
    print(f'  Rate-limited:            {len(rate_limited)}')
    print(f'  Errored:                 {len(errored)}')
    print()

    if redirected:
        print('REDIRECTED — Apollo slug is stale, redirect target is the real one:')
        for slug, name, url, res in redirected:
            print(f"  - {name:<25} {slug} -> {res['final_slug']}")
        print()
    if not_found:
        print('NOT FOUND — slug is dead on LinkedIn (Apollo cached bogus data):')
        for slug, name, url, res in not_found:
            print(f"  - {name:<25} {slug}")
        print()

    # ── Write verification CSV (step1c-compatible format) ─────────────────────
    actionable = redirected + not_found
    if actionable:
        out_path = os.path.join(
            project_dir,
            f'{args.project_name}_step1c_linkedin_check.csv',
        )
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['company_name', 'current_domain', 'correct_domain',
                        'linkedin_company_url', 'status', 'notes'])
            for slug, name, url, res in actionable:
                if res['status'] == 'redirect':
                    new_url = f"https://www.linkedin.com/company/{res['final_slug']}"
                    w.writerow([name, '', '', new_url, 'FOUND',
                                f'LinkedIn rebrand: {slug} -> {res["final_slug"]}'])
                else:
                    w.writerow([name, '', '', '', 'NOT_FOUND',
                                f'LinkedIn returned 404 for slug {slug!r}'])
        print(f'Verification CSV written: {out_path}')
        print('Apply via:')
        print(f'  python3 step1c_domain_verification.py {args.project_name} \\')
        print(f'      --input <step2_companies_with_linkedin.csv> \\')
        print(f'      --corrections {out_path}')
    else:
        print('No actionable issues found.')


if __name__ == '__main__':
    main()
