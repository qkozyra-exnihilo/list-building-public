#!/usr/bin/env python3
"""
step1d_icp_homepage_check.py — ICP qualification via homepage analysis
======================================================================
Fetches each company's homepage and classifies it as B2B SaaS or not using
a Claude Haiku classifier. Works on any list (pre or post Apollo). If the
homepage is unreachable, falls back to Apollo DB data when available.

Outputs two files:
  - {project}_step1d_classification.csv
        domain, company_name, classification, vertical, confidence, reason, source
  - {project}_step1d_icp_filtered.csv
        input CSV filtered to classification in {saas, unclear} (keep for review)

Usage:
    python3 step1d_icp_homepage_check.py <project_name> \
        --input <companies.csv> \
        --domain-col "Company Website"

    # Re-apply a previous classification without re-fetching:
    python3 step1d_icp_homepage_check.py <project_name> \
        --input <companies.csv> \
        --domain-col "Company Website" \
        --classification <step1d_classification.csv>

Env: ANTHROPIC_API_KEY must be set.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from anthropic import Anthropic

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

LLM_PROMPT = """Classify this company's homepage. Answer ONLY with valid JSON.

Fields:
- verdict: "saas" | "not_saas" | "unclear"
- vertical: short label (e.g. "fintech", "healthtech", "hr tech", "edtech", "devtools", "proptech", "horizontal saas", "not saas")
- confidence: 0.0 to 1.0
- reason: one sentence

Definition of SaaS: a B2B software product sold as a subscription (or usage-based) service — has a product, has customers (not individual consumers), has a website meant to sell/explain the product. Vertical SaaS (software for a specific industry) still counts as SaaS.

NOT SaaS: agencies, consulting firms, physical goods, retail, marketplaces that are not software-first, professional services where the software is incidental, personal blogs, parked domains.

Company: {name}
Homepage title: {title}
Meta description: {meta}
Homepage text (first 2000 chars):
{body}
"""


# ── Fetching ────────────────────────────────────────────────────────────────

def split_domains(cell):
    if not cell:
        return []
    parts = re.split(r"[,\s;|]+", cell.strip())
    return [p.strip().lower() for p in parts if p.strip()]


def normalize_url(domain):
    domain = domain.strip().lower()
    if domain.startswith(("http://", "https://")):
        return domain
    return f"https://{domain}"


def _curl_fetch(url, timeout=15):
    """Curl fallback; cracks some Cloudflare 403s that block Python requests."""
    try:
        proc = subprocess.run(
            [
                "curl", "-sL", "--compressed",
                "--max-time", str(timeout),
                "-A", HEADERS["User-Agent"],
                "-H", f"Accept: {HEADERS['Accept']}",
                "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
                "-w", "\n%{http_code}\n%{url_effective}",
                url,
            ],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        out = proc.stdout
        parts = out.rsplit("\n", 2)
        if len(parts) < 3:
            return None, None, None
        html, status, final = parts[0], parts[1].strip(), parts[2].strip()
        try:
            status_int = int(status)
        except ValueError:
            return None, None, None
        if status_int >= 400 or not html.strip():
            return status_int, None, final or url
        return status_int, html, final or url
    except Exception:
        return None, None, None


def fetch_homepage(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 403 or (r.status_code >= 400 and not r.text.strip()):
            c_status, c_html, c_url = _curl_fetch(url, timeout=timeout + 5)
            if c_html:
                return c_status, c_html, c_url
        return r.status_code, r.text, r.url
    except Exception:
        try:
            alt = url.replace("https://", "http://")
            r = requests.get(alt, headers=HEADERS, timeout=timeout, allow_redirects=True)
            return r.status_code, r.text, r.url
        except Exception:
            return _curl_fetch(url, timeout=timeout + 5)


def extract_homepage_text(html):
    if not html:
        return "", "", ""
    m = re.search(r"<title[^>]*>([^<]*)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = (m.group(1).strip()[:300]) if m else ""
    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        html, flags=re.IGNORECASE,
    )
    meta = (m.group(1).strip()[:500]) if m else ""
    if not meta:
        m = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            html, flags=re.IGNORECASE,
        )
        meta = (m.group(1).strip()[:500]) if m else ""
    body = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()[:2000]
    return title, meta, body


# ── Classification ──────────────────────────────────────────────────────────

def classify_with_llm(client, name, title, meta, body):
    prompt = LLM_PROMPT.format(
        name=name,
        title=title or "(none)",
        meta=meta or "(none)",
        body=body or "(none)",
    )
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"json_error: {e}"
    except Exception as e:
        return None, f"api_error: {e}"


def classify_company(client, name, domain, apollo_rec=None):
    """Return dict: {classification, vertical, confidence, reason, source, fetch_status, fetched_domain}."""
    result = {
        "classification": "unreachable",
        "vertical": "",
        "confidence": "",
        "reason": "",
        "source": "",
        "fetch_status": "",
        "fetched_domain": "",
    }
    status, html, final_url = None, None, None
    for candidate in split_domains(domain):
        url = normalize_url(candidate)
        status, html, final_url = fetch_homepage(url)
        if status and 200 <= status < 400 and html:
            result["fetched_domain"] = candidate
            break
    result["fetch_status"] = str(status) if status else ""

    if status and 200 <= status < 400 and html:
        title, meta, body = extract_homepage_text(html)
        data, err = classify_with_llm(client, name, title, meta, body)
        if data:
            result["source"] = "homepage"
            result["classification"] = data.get("verdict", "unclear")
            result["vertical"] = data.get("vertical", "")
            result["confidence"] = str(data.get("confidence", ""))
            result["reason"] = data.get("reason", "")
            return result
        result["reason"] = err or "llm_error"

    # Apollo fallback (unreachable homepage)
    if apollo_rec:
        desc = (apollo_rec.get("short_description") or "").strip()
        kw = (apollo_rec.get("keywords") or "").strip()
        industry = (apollo_rec.get("industry") or "").strip()
        if desc or kw:
            body = f"Industry: {industry}\nKeywords: {kw}\nDescription: {desc}"[:2000]
            data, err = classify_with_llm(client, name, title="", meta=industry, body=body)
            if data:
                result["source"] = "apollo_fallback"
                result["classification"] = data.get("verdict", "unclear")
                result["vertical"] = data.get("vertical", "")
                result["confidence"] = str(data.get("confidence", ""))
                result["reason"] = data.get("reason", "")
                return result

    return result


# ── DB loader ───────────────────────────────────────────────────────────────

def load_apollo_db(path):
    db = {}
    if not path or not os.path.exists(path):
        return db
    with open(path) as f:
        for row in csv.DictReader(f):
            d = (row.get("domain") or "").strip().lower()
            if d:
                db[d] = row
    return db


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_name")
    parser.add_argument("--input", required=True)
    parser.add_argument("--domain-col", default="Company Website")
    parser.add_argument("--name-col", default=None,
                        help="Column for company name (auto-detects common variants)")
    parser.add_argument("--classification", default=None,
                        help="Pre-existing classification CSV to apply (skip fetching)")
    parser.add_argument("--min-saas-confidence", type=float, default=0.7,
                        help="Min LLM confidence to KEEP as saas")
    parser.add_argument("--min-drop-confidence", type=float, default=0.8,
                        help="Min LLM confidence to DROP as not_saas")
    parser.add_argument("--workers", type=int, default=15)
    parser.add_argument("--apollo-db",
                        default=os.path.expanduser(
                            "~/github/list-building/database/apollo_companies_database.csv"),
                        help="Apollo DB path for unreachable-homepage fallback")
    args = parser.parse_args()

    # Load input
    with open(args.input, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Input: {len(rows)} rows")

    # Resolve name column
    name_col = args.name_col
    if not name_col:
        for cand in ["Company Name", "company_name", "Record", "name"]:
            if rows and cand in rows[0]:
                name_col = cand
                break
    if not name_col:
        print(f"ERROR: could not find name column. Use --name-col.", file=sys.stderr)
        sys.exit(1)

    # Build unique domain -> name map (uses first candidate from split_domains)
    domains = {}  # domain_str (original multi-domain cell) -> company_name
    for r in rows:
        d = r.get(args.domain_col, "").strip()
        if not d:
            continue
        if d not in domains:
            domains[d] = r.get(name_col, d)
    print(f"Unique domain cells: {len(domains)}")

    # Classify
    cls_file = f"{args.project_name}_step1d_classification.csv"

    if args.classification:
        with open(args.classification) as f:
            classifications = {r["domain"]: r for r in csv.DictReader(f)}
        print(f"Loaded {len(classifications)} pre-existing classifications")
    else:
        apollo_db = load_apollo_db(args.apollo_db)
        print(f"Loaded {len(apollo_db)} Apollo DB entries for fallback")

        def get_apollo(domain):
            for d in split_domains(domain):
                if d in apollo_db:
                    return apollo_db[d]
            return None

        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        print(f"Classifying {len(domains)} homepages ({args.workers} workers)...", flush=True)
        t0 = time.time()
        classifications = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {
                pool.submit(classify_company, client, name, d, get_apollo(d)): d
                for d, name in domains.items()
            }
            done = 0
            for fut in as_completed(futs):
                d = futs[fut]
                res = fut.result()
                classifications[d] = {"domain": d, "company_name": domains[d], **res}
                done += 1
                if done % 25 == 0 or done == len(futs):
                    print(f"  {done}/{len(futs)} ({time.time()-t0:.0f}s)", flush=True)

        with open(cls_file, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["domain", "company_name", "classification", "vertical",
                            "confidence", "reason", "source", "fetch_status", "fetched_domain"],
            )
            w.writeheader()
            for d in sorted(classifications.keys()):
                w.writerow(classifications[d])
        print(f"Classification saved to {cls_file}")

    # Report
    counts = Counter(c.get("classification", "unknown") for c in classifications.values())
    print(f"\n{'='*60}")
    print(f"ICP HOMEPAGE CHECK RESULTS")
    print(f"{'='*60}")
    for k in ("saas", "unclear", "not_saas", "unreachable"):
        print(f"  {k:<15} {counts.get(k, 0)}")

    # Keep/drop logic
    def should_keep(entry):
        c = entry.get("classification", "")
        try:
            conf = float(entry.get("confidence") or 0)
        except ValueError:
            conf = 0
        if c == "saas" and conf >= args.min_saas_confidence:
            return "keep"
        if c == "not_saas" and conf >= args.min_drop_confidence:
            return "drop"
        # unclear, low confidence, unreachable → manual review (keep)
        return "review"

    decisions = {d: should_keep(c) for d, c in classifications.items()}
    n_keep = sum(1 for v in decisions.values() if v == "keep")
    n_review = sum(1 for v in decisions.values() if v == "review")
    n_drop = sum(1 for v in decisions.values() if v == "drop")
    print(f"\n  KEEP (saas high conf):     {n_keep}")
    print(f"  REVIEW (unclear/low conf): {n_review}")
    print(f"  DROP (not_saas high conf): {n_drop}")

    # Filter input rows — keep only those NOT dropped
    keep_rows = []
    for r in rows:
        d = r.get(args.domain_col, "").strip()
        if decisions.get(d) == "drop":
            continue
        keep_rows.append(r)

    out_file = f"{args.project_name}_step1d_icp_filtered.csv"
    if keep_rows:
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(keep_rows)
    print(f"\n  Output: {len(keep_rows)} rows kept ({len(rows)-len(keep_rows)} dropped)")
    print(f"  Written to {out_file}")


if __name__ == "__main__":
    main()
