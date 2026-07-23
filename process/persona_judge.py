#!/usr/bin/env python3
"""
persona_judge.py — LLM persona fit judge (non-deterministic layer)
==================================================================
Used by Step 4 as the PRIMARY persona classifier. The deterministic keyword
lists in step4 are demoted to (a) Stage-A noise removal and (b) an offline
fallback. Everything that survives Stage A is judged here so that title
variants the keyword dictionary never anticipated (e.g. "Finance Lead",
"Finance Operations", "Manager, Finance EMEA") are assessed on their merits.

Design goals:
  - Hybrid, not a replacement: keywords kill guaranteed noise cheaply; the LLM
    judges the rest. The LLM is never gated behind keyword *inclusion*, so it
    never misses a finance-ops-style title.
  - Reproducible: temperature 0 + structured JSON + a persistent verdict cache
    keyed by (title | headline | employee-bucket). Re-runs are free and stable.
  - Auditable: every verdict carries a short `reason`. Nothing is dropped
    silently — the caller logs why.
  - Resilient: on API/parse failure the caller falls back to deterministic
    categorization, so a network blip never loses contacts.

Cache: database/persona_judgments.csv (cumulative, shared across projects —
titles repeat heavily, so the cache pays for itself fast).
"""

import csv, hashlib, json, os, re, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

MODEL = "claude-haiku-4-5"
MAX_WORKERS = 8

VALID_CATEGORIES = {"Finance", "Revenue", "Ops"}
# Priority order (lower = higher priority). Mirrors step4's CATEGORIES order.
CATEGORY_PRIORITY = {"Finance": 0, "Revenue": 1, "Ops": 2}

CACHE_FIELDS = ["cache_key", "title", "headline", "emp_bucket",
                "keep", "category", "seniority_tier", "fit_score", "reason"]

SYSTEM_RUBRIC = """You are a B2B sales-list persona classifier for Hyperline, a billing and \
revenue-management platform. Given a person's job title and profile, decide whether they are a \
relevant buying-influence contact for an outbound campaign.

TARGET PERSONAS, in priority order:
1. Finance — finance / accounting / FP&A / finance-operations, at ANY seniority INCLUDING \
junior (e.g. Finance Lead, Finance Operations, Finance Manager, FP&A Analyst, CFO, DAF, \
Directeur Financier, Head of Finance, VP Finance). EXCLUDE Controllers (Controller, Financial \
Controller, Group Controller, Contrôleur de gestion) — too operational/backward-looking — and \
purely clerical / transactional bookkeeping with no analytical or ownership scope (a plain \
"Accountant", "Comptable", "Accounts Payable Clerk", "AP clerk", "Aide comptable"). A controller \
who ALSO holds a qualifying finance-leadership title (e.g. "CFO & Group Controller") is kept by \
that title.
2. Revenue — the revenue team: (a) revenue LEADERSHIP (CRO, Chief Revenue Officer, VP Revenue, \
Head of Revenue, Revenue Director, Director of Revenue, CCO, Chief Commercial Officer) AND \
(b) Revenue Operations / RevOps at ANY seniority INCLUDING junior (RevOps Analyst, Revenue \
Operations Specialist, Revenue Operations Manager, Head of Revenue Operations). Revenue \
Operations ALWAYS belongs here (Revenue), never under Ops.
3. Ops — operations roles, but ONLY at "Head of" level or above. INCLUDE: general operations / \
COO (Head of Operations, VP Operations, Director of Operations, COO, Directeur des Opérations), \
Business Operations (Head of Business Ops, VP / Director of Business Ops), Sales Operations \
(Head of Sales Ops, VP / Director of Sales Ops), and Growth Operations (Head of / VP / Director \
of Growth Ops). EXCLUDE any of these below Head-of (Operations Manager, Sales Operations \
Manager, Ops Analyst / Specialist / Coordinator). EXCLUDE General Manager. EXCLUDE wrong ops \
types entirely: Marketing Ops, IT / Technical Ops, DevOps, People / HR Ops, Customer Ops, \
Product Ops, AI Ops. \
CRITICAL: COO / Chief Operating Officer and Director / VP / Head of (general) Operations are \
ALWAYS kept here on TITLE ALONE — do NOT require extra "scope evidence" in the headline, and \
do NOT treat COO as "general management". COO is a qualifying Ops persona (unlike CEO / \
President / GM / Managing Director, which ARE excluded).

NEVER relevant (keep=false): founders and general management — Founder, Co-Founder, CEO, \
President, General Manager, GM, Managing Director, Gérant, PDG, DG, Directeur Général, Dirigeant \
— at ANY company size (NOTE: COO / Chief Operating Officer is NOT part of this exclusion — it is \
a kept Ops persona per #3; a combined founder+COO title such as "COO & Co-Founder" is kept by the \
COO qualifying role); PURE SALES roles at any level — Head of Sales, VP Sales, Sales Director, \
Chief Sales Officer / CSO, Head of Commercial, Directeur Commercial, Directeur des Ventes, \
Account Executive, Account Manager, Key Account Manager, SDR, BDR, Business Developer, Sales \
Manager, Partnerships Manager (Sales is NO LONGER a target persona); plus investors, board \
members, advisors, VCs, angels, students, interns, freelancers, consultants, fractional/interim, \
and wrong C-suite (CTO, CPO, CMO, CIO, CDO, Chief Scientific Officer, Chief of Staff).
IMPORTANT disambiguation: Sales OPERATIONS at Head-of+ is still KEPT (Ops, #3), and \
CCO / Chief Commercial Officer is still KEPT (Revenue, #2) — only PURE sales roles are dropped.

Judge by the title PLUS the headline/summary — they reveal real scope. A "Manager, Finance EMEA" \
who manages a team and owns regional reporting is a genuine finance decision-influencer → \
keep=true, Finance. A person whose ONLY role is founder / CEO / President / GM / pure-sales is \
keep=false, but if they ALSO hold a qualifying Finance / Revenue / Ops role (e.g. "CEO & CFO", \
"Head of Sales & Head of Revenue Operations"), classify them by that qualifying role. When a \
person fits more than one category, pick the HIGHEST-priority one (Finance > Revenue > Ops).

Respond with ONLY a JSON object, no prose:
{"keep": <bool>, "category": "Finance"|"Revenue"|"Ops"|null, \
"seniority_tier": "C-level"|"VP"|"Head"|"Director"|"Lead"|"Manager"|"IC"|"Other", \
"fit_score": <int 0-100, how strong a buying influence for a billing/revenue product>, \
"reason": "<= 20 words"}
If keep is false, category MUST be null."""


def emp_bucket(emp):
    """Coarse employee bucket so the cache generalizes across exact headcounts."""
    if emp is None:
        return "unknown"
    if emp <= 50:
        return "<=50"
    if emp <= 200:
        return "51-200"
    if emp <= 1000:
        return "201-1000"
    return "1000+"


def make_cache_key(title, headline, bucket):
    raw = f"{(title or '').strip().lower()}|{(headline or '').strip().lower()}|{bucket}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_cache(cache_path):
    cache = {}
    if os.path.isfile(cache_path):
        with open(cache_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cache[row["cache_key"]] = {
                    "keep": row["keep"] == "True",
                    "category": row["category"] or None,
                    "seniority_tier": row["seniority_tier"],
                    "fit_score": int(row["fit_score"] or 0),
                    "reason": row["reason"],
                }
    return cache


def append_cache(cache_path, records):
    """Append new verdicts to the cumulative cache (create with header if absent)."""
    if not records:
        return
    exists = os.path.isfile(cache_path)
    with open(cache_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerows(records)


def _parse_verdict(text):
    """Extract the JSON object from the model response and normalize it."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in response: {text[:120]}")
    v = json.loads(m.group(0))
    keep = bool(v.get("keep"))
    cat = v.get("category")
    if cat not in VALID_CATEGORIES:
        cat = None
    if not keep:
        cat = None
    try:
        fit = int(v.get("fit_score") or 0)
    except (TypeError, ValueError):
        fit = 0
    return {
        "keep": keep and cat is not None,
        "category": cat,
        "seniority_tier": str(v.get("seniority_tier") or "Other")[:20],
        "fit_score": max(0, min(100, fit)),
        "reason": str(v.get("reason") or "")[:200],
    }


def _judge_one(client, title, headline, summary, company, emp, location):
    user = (
        f"Title: {title or '(none)'}\n"
        f"Headline: {headline or '(none)'}\n"
        f"Summary: {(summary or '')[:600]}\n"
        f"Company: {company or '(unknown)'} ({emp if emp is not None else '?'} employees)\n"
        f"Location: {location or '(unknown)'}"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=200,
        temperature=0,
        system=[{"type": "text", "text": SYSTEM_RUBRIC,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return _parse_verdict(resp.content[0].text)


def judge_contacts(items, cache_path, verbose=True):
    """
    items: list of dicts with keys: title, headline, summary, company, emp (int|None), location
    Returns: list of verdict dicts aligned to `items`. On per-item API/parse
             failure the verdict is {"keep": None, "error": "..."} so the caller
             can fall back to deterministic logic for that one contact.
    """
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK not installed (pip install anthropic)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic()
    cache = load_cache(cache_path)
    results = [None] * len(items)
    new_records = []
    rec_lock = threading.Lock()
    to_call = []  # (idx, key, item)

    # Resolve from cache first
    for idx, it in enumerate(items):
        bucket = emp_bucket(it.get("emp"))
        key = make_cache_key(it.get("title"), it.get("headline"), bucket)
        if key in cache:
            results[idx] = dict(cache[key])
        else:
            to_call.append((idx, key, it))

    cache_hits = len(items) - len(to_call)
    if verbose:
        print(f"  Persona judge: {len(items)} contacts "
              f"({cache_hits} cached, {len(to_call)} to call, model={MODEL})")

    done = [0]

    def work(idx, key, it):
        try:
            v = _judge_one(client, it.get("title"), it.get("headline"),
                           it.get("summary"), it.get("company"),
                           it.get("emp"), it.get("location"))
            with rec_lock:
                rec = {"cache_key": key, "title": (it.get("title") or "")[:120],
                       "headline": (it.get("headline") or "")[:120],
                       "emp_bucket": emp_bucket(it.get("emp")), **v}
                new_records.append(rec)
            return idx, v
        except Exception as e:  # noqa: BLE001 — resilient by design
            return idx, {"keep": None, "error": str(e)[:160]}

    if to_call:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = [ex.submit(work, idx, key, it) for idx, key, it in to_call]
            for fut in as_completed(futures):
                idx, v = fut.result()
                results[idx] = v
                done[0] += 1
                if verbose and done[0] % 25 == 0:
                    print(f"    judged {done[0]}/{len(to_call)} ...")

    append_cache(cache_path, new_records)
    return results
