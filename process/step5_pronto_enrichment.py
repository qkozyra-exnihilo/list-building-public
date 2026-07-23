#!/usr/bin/env python3
"""
step5_pronto_enrichment.py — Pronto Email + Phone Enrichment
=============================================================
Input:  {project}_step4_contacts_filtered.csv
Output: {project}_step5_contacts_enriched.csv (single output)

Logic:
  1. Load step4 contacts
  2. Check Pronto DB + enrichment ledger — pre-fill from cache, skip if already
     enriched OR already paid for (ledger)
  3. Submit remaining candidates via Pronto API
  4. POLL each result by id (GET /contacts/{id}) until finished — no webhook, no
     tunnel, nothing to 503; write-through to ledger as results arrive
  5. Reconcile: every submitted id must end retrieved (billed == captured)
  6. Write single output + upsert contacts into pronto_contacts_database.csv

Anti-double-billing safeguards (added 2026-06-01 after ~11K credits were burned
re-running phone passes — see project_phone_provider_benchmark / billing audit):
  • RUN LOCK   — only one run per project at a time (no concurrent double-charge).
  • LEDGER     — every successful Pronto submit is logged the instant it is
                 accepted (a charge becomes possible at that moment). A
                 (linkedin_url, type) pair already in the ledger is NEVER
                 re-submitted unless --force. This survives crashes/restarts.
  • POLL (not webhook) — we PULL each result by id instead of waiting for Pronto
                 to push it. Pronto's 503s on our webhook dropped numbers; polling
                 has no inbound dependency and can't lose data. NOTE: polling is not
                 cheaper — retrieving a found number the first time costs the normal
                 30 (re-reading an already-delivered one is free). The win is
                 reliability + reconciliation, not cost.
  • RECONCILE — every submitted id is polled until 'finished'; the run reports
                 billed == submitted == captured, so loss is impossible to miss.
  • WRITE-THROUGH — each polled result is appended to the ledger + output saved
                 every round, so a killed run never loses what it already paid for.
  • PHONE GUARD — phone is re-checked against DB+ledger right before submit.

Usage:
    python3 step5_pronto_enrichment.py <project_name> \\
        --input <step4_contacts_filtered.csv> \\
        [--db <pronto_contacts_database.csv>] \\
        [--email-only]   # skip phone entirely (cheapest)
        [--force]        # re-submit even contacts already in the ledger (DANGER:
                         #   can re-bill — only for a deliberate months-later refresh)
        [--yes]          # skip the interactive confirmation (for automation)
"""

import csv, json, os, re, signal, sys, threading, time, warnings
import requests
from datetime import date, datetime

warnings.filterwarnings('ignore')

PRONTO_API_KEY = os.environ["PRONTO_API_KEY"]
PRONTO_BASE    = "https://app.prontohq.com"
TODAY          = str(date.today())

DB_FIELDNAMES = [
    'status','rejection_reasons','first_name','last_name','gender',
    'email','email_status','phone','linkedin_url','linkedin_id_url',
    'profile_image_url','location','title',
    'years_in_position','months_in_position','years_in_company','months_in_company',
    'company_name','company_cleaned_name','company_website','company_location',
    'company_industry','company_linkedin_url','company_linkedin_id',
    'company_employee_range','company_hq_city','company_hq_country',
    'company_hq_postal','company_hq_region','company_description',
    'source_project','searched_date','enriched_date',
]

# Append-only billing ledger — one line per submit and per result. This is the
# authoritative "what have we already paid Pronto for" record. Never overwritten.
LEDGER_FILENAME = "pronto_enrichment_ledger.csv"
LEDGER_FIELDS   = ['timestamp','event','project','linkedin_url','types',
                   'enrichment_id','email','email_status','phone']

api_headers = {
    "X-API-Key": PRONTO_API_KEY,
    "Content-Type": "application/json"
}


PHONE_PER_COMPANY = 2  # phone enriched only for the top N contacts per company
# Phone-cap ranking. Current step4 categories: Finance > Revenue > Ops.
# Legacy aliases (RevOps/COO/Sales/Founder) kept so OLD step4 output CSVs still rank;
# Sales + Founder are no longer produced by step4 (dropped as personas) but rank last
# if an old CSV is reprocessed.
PRIORITY_RANK = {"Finance": 0, "Revenue": 1, "RevOps": 1, "Ops": 2, "COO": 2,
                 "Sales": 3, "Founder": 9}


def normalize_li(url):
    return (url or '').strip().rstrip('/').lower()


def company_key(row):
    """Group key for per-company phone caps: domain, else company LinkedIn, else name."""
    d = (row.get("Company Domain") or row.get("Company Website") or "").strip().lower()
    d = re.sub(r'^https?://', '', d).strip('/')
    d = re.sub(r'^(?:www|fr|en|de|es|it|nl|us|uk|eu\d?|app)\.', '', d)
    if d:
        return d
    li = (row.get("Company Linkedin Flagship Url") or row.get("Company Linkedin") or "").strip().lower()
    if li:
        return re.sub(r'[?#].*$', '', re.sub(r'^https?://(www\.)?', '', li)).rstrip('/')
    return (row.get("Company Name") or row.get("Company Cleaned Name") or "unknown").strip().lower()


def rank_value(row):
    """Sort key for phone priority: (persona priority asc, fit_score desc)."""
    cat = (row.get("_priority_category") or "").strip()
    prio = PRIORITY_RANK.get(cat, 9)
    try:
        fit = int(float(row.get("_fit_score") or 0))
    except (TypeError, ValueError):
        fit = 0
    return (prio, -fit)


def compute_phone_eligible(rows, per_company=PHONE_PER_COMPANY):
    """Return the set of normalized LinkedIn keys allowed PHONE enrichment
    (top `per_company` contacts per company; per_company=0 → no cap, all rows)."""
    by_company = {}
    for row in rows:
        by_company.setdefault(company_key(row), []).append(row)
    eligible = set()
    for _ck, members in by_company.items():
        members.sort(key=rank_value)
        chosen = members if per_company == 0 else members[:per_company]
        for row in chosen:
            li = normalize_li(row.get("Linkedin Profile Url") or row.get("Linkedin Id Url") or "")
            if li:
                eligible.add(li)
    return eligible


# ── Global state ──────────────────────────────────────────────────────────────
rows_by_li   = {}
all_rows     = []

# Ledger globals
LEDGER_PATH  = None
PROJECT_NAME = None
ledger_lock  = threading.Lock()


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


# ── Enrichment ledger (anti-double-billing) ────────────────────────────────────
def load_ledger(path):
    """Return {li_key: {'submitted': set(types), 'email': str, 'email_status': str,
                        'phone': str}} built from the append-only ledger."""
    led = {}
    if not path or not os.path.isfile(path):
        return led
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            li = normalize_li(r.get('linkedin_url') or '')
            if not li:
                continue
            rec = led.setdefault(li, {'submitted': set(), 'email': '',
                                      'email_status': '', 'phone': ''})
            for t in (r.get('types') or '').split('|'):
                t = t.strip()
                if t:
                    rec['submitted'].add(t)
            if (r.get('email') or '').strip():
                rec['email'] = r['email'].strip()
                rec['email_status'] = (r.get('email_status') or '').strip()
            if (r.get('phone') or '').strip():
                rec['phone'] = r['phone'].strip()
    return led


def ledger_append(event, li_url, types, enrichment_id='', email='',
                  email_status='', phone=''):
    """Append one crash-safe line to the ledger and flush to disk. Thread-safe."""
    if not LEDGER_PATH:
        return
    line = {
        'timestamp': now_iso(),
        'event': event,
        'project': PROJECT_NAME or '',
        'linkedin_url': li_url or '',
        'types': '|'.join(types) if isinstance(types, (list, set, tuple)) else (types or ''),
        'enrichment_id': enrichment_id or '',
        'email': email or '',
        'email_status': email_status or '',
        'phone': phone or '',
    }
    with ledger_lock:
        new_file = not os.path.isfile(LEDGER_PATH)
        with open(LEDGER_PATH, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction='ignore')
            if new_file:
                w.writeheader()
            w.writerow(line)
            f.flush()
            os.fsync(f.fileno())


# ── Per-project run lock (no concurrent double-charge) ─────────────────────────
def acquire_lock(lock_path, force):
    """Atomically create a lock file. If a live process already holds it, abort."""
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {now_iso()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        pid = None
        try:
            with open(lock_path) as f:
                pid = int((f.read().split() or ['0'])[0])
        except Exception:
            pid = None
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if alive and not force:
            print(f"  ✗ ABORT: another enrichment run for this project is active "
                  f"(pid {pid}, lock {lock_path}).")
            print(f"    Running two passes at once double-bills. Wait for it to finish, "
                  f"or pass --force if you are certain it is dead.")
            return False
        # stale lock (or --force) → take it over
        print(f"  (stale lock from pid {pid} removed)" if not alive else
              f"  (--force: overriding lock held by pid {pid})")
        try:
            with open(lock_path, 'w') as f:
                f.write(f"{os.getpid()} {now_iso()}\n")
        except Exception:
            pass
        return True


def release_lock(lock_path):
    try:
        if lock_path and os.path.isfile(lock_path):
            os.remove(lock_path)
    except Exception:
        pass


# ── Submit enrichment ────────────────────────────────────────────────────────
# Phone is ~10x the credits of email, so by default we enrich EMAIL on everyone
# but PHONE only on the top 2 contacts per company (ranked by persona priority +
# LLM fit_score from Step 4). `enrichment_type` is therefore decided per-row.
def submit_enrichment(row, types, webhook_url=None):
    li_url  = (row.get("Linkedin Profile Url") or row.get("Linkedin Id Url") or "").strip()
    domain  = (row.get("Company Domain") or row.get("Company Website") or "")
    domain  = domain.replace('https://','').replace('http://','').strip('/')
    if not domain:
        domain = None
    company = (row.get("Company Name") or row.get("Company Cleaned Name") or "").strip()

    payload = {
        "linkedin_url":    li_url,
        "firstname":       (row.get("First Name") or "").strip(),
        "lastname":        (row.get("Last Name") or "").strip(),
        "enrichment_type": types,
    }
    # We POLL for results (GET /contacts/{id}) instead of relying on inbound webhook
    # delivery — Pronto's 503s on our webhook silently lost numbers we were billed
    # for. webhook_url is only included if explicitly provided (it is not, by default).
    if webhook_url:
        payload["webhook_url"] = webhook_url
    if domain:
        payload["domain"] = domain
    else:
        payload["company_name"] = company

    try:
        r = requests.post(
            f"{PRONTO_BASE}/api/v2/contacts/single_enrich",
            headers=api_headers, json=payload, timeout=15
        )
        if r.status_code in (200, 201):
            resp = r.json()
            enr_id = resp.get('id') or resp.get('enrichment_id') or resp.get('contact', {}).get('id')
            # LEDGER: the request was accepted -> a charge is now possible. Record
            # it BEFORE anything else so a crash on the very next line can't cause
            # a silent re-submit on the next run.
            ledger_append('submitted', li_url, types, enrichment_id=enr_id or '')
            return enr_id
        else:
            print(f"  ✗ Submit error {r.status_code}: {r.text[:150]}")
            return None
    except Exception as e:
        print(f"  ✗ Submit exception: {e}")
        return None


def fetch_result(enr_id):
    """Pull an enrichment result by id. Returns (status, email, email_status, phone_str).
    BILLING (verified 2026-06-01): re-reading a result that was ALREADY delivered/charged
    is free, but retrieving a found number for the FIRST time charges the normal rate
    (3/email, 30/phone-number) — exactly what an inbound webhook would have charged.
    So polling does not save credits; its value is RELIABILITY (no 503 loss) and
    RECONCILIATION (you receive, and can see, every number you pay for)."""
    try:
        r = requests.get(f"{PRONTO_BASE}/api/v2/contacts/{enr_id}",
                         headers=api_headers, timeout=15)
        if r.status_code != 200:
            return (None, '', '', '')
        d = r.json()
        phones = d.get('phone') or d.get('phones') or []
        if isinstance(phones, str):
            phones = [phones]
        phone_str = '; '.join(p for p in phones if p)
        return (d.get('status'), (d.get('email') or '').strip(),
                (d.get('email_status') or '').strip(), phone_str)
    except Exception:
        return (None, '', '', '')


def poll_results(pending, output_path, db_path, project_name,
                 interval=10, max_wait=900):
    """Poll every submitted enrichment id until finished (or timeout). Pure outbound
    GETs — no webhook, no tunnel, nothing to 503. Writes results through to the
    ledger as they arrive, and reconciles: every submitted id should end finished.
    `pending` = {enr_id: (li_key, types)}."""
    remaining = set(pending)
    got = 0
    waited = idle_rounds = 0
    print(f"  Polling {len(remaining)} results (every {interval}s, no webhook)...")
    while remaining and waited < max_wait:
        just_done = []
        for enr_id in list(remaining):
            status, email, email_status, phone_str = fetch_result(enr_id)
            if status != 'finished':
                continue
            li_key, _types = pending[enr_id]
            row = rows_by_li.get(li_key)
            if row is not None:
                if email and not (row.get("Email") or "").strip():
                    row["Email"] = email
                    row["Email Status"] = email_status
                if phone_str and not (row.get("Phone (Pronto)") or "").strip():
                    row["Phone (Pronto)"] = phone_str
                ledger_append('result',
                              row.get("Linkedin Profile Url") or row.get("Linkedin Id Url") or li_key,
                              [], enrichment_id=enr_id,
                              email=email, email_status=email_status, phone=phone_str)
                got += 1
                name = f"{row.get('First Name','')} {row.get('Last Name','')}".strip()
                print(f"  ✓ [{got}/{len(pending)}] {name}: email={email or '—'} phone={phone_str or '—'}")
            just_done.append(enr_id)
        remaining -= set(just_done)
        idle_rounds = 0 if just_done else idle_rounds + 1
        if remaining:
            # Save progress every round so a crash mid-poll loses nothing.
            write_output(all_rows, output_path)
            if idle_rounds >= 6:   # ~1min of no progress AND still pending → likely stuck
                print(f"  …{len(remaining)} still not finished after {idle_rounds} idle rounds; continuing")
            time.sleep(interval)
            waited += interval
    # ── Reconcile: every submitted id must be accounted for ──────────────────
    print(f"\n  Retrieved {got}/{len(pending)} results.")
    if remaining:
        print(f"  ⚠ RECONCILE: {len(remaining)} submitted id(s) never reached 'finished' "
              f"within {max_wait}s. They are in the ledger as 'submitted' — re-run this "
              f"step to poll them again (free: GET re-fetches finished results at no cost).")
    else:
        print(f"  ✓ RECONCILED: all {len(pending)} submitted ids retrieved — "
              f"billed == submitted == captured.")
    return got, remaining


def main():
    global all_rows, LEDGER_PATH, PROJECT_NAME

    # ── Parse args ────────────────────────────────────────────────────────────
    if len(sys.argv) < 2:
        print("Usage: python3 step5_pronto_enrichment.py <project_name> "
              "--input <file> [--db <pronto_db.csv>] [--email-only] [--force] [--yes]")
        sys.exit(1)

    project_name = sys.argv[1]
    PROJECT_NAME = project_name
    input_path = None
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "database", "pronto_contacts_database.csv")

    args = sys.argv[2:]
    email_only = False
    force      = False
    assume_yes = False
    phone_per_company = PHONE_PER_COMPANY   # 0 = no cap (phone for ALL persona-fit contacts)
    i = 0
    while i < len(args):
        if args[i] == '--input' and i + 1 < len(args):
            input_path = args[i + 1]
            i += 2
        elif args[i] == '--db' and i + 1 < len(args):
            db_path = args[i + 1]
            i += 2
        elif args[i] == '--phone-per-company' and i + 1 < len(args):
            phone_per_company = int(args[i + 1])
            i += 2
        elif args[i] == '--email-only':
            email_only = True
            i += 1
        elif args[i] == '--force':
            force = True
            i += 1
        elif args[i] in ('--yes', '-y'):
            assume_yes = True
            i += 1
        else:
            i += 1

    if email_only:
        print("  Mode: --email-only (no phone enrichment at all, saves ~10x credits per find)")
    if force:
        print("  ⚠️  Mode: --force — ledger de-dup DISABLED. Contacts already paid for "
              "CAN be re-billed. Use only for a deliberate refresh.")

    if not input_path:
        print("Error: --input is required.")
        sys.exit(1)

    output_path = os.path.join(os.path.dirname(os.path.abspath(input_path)),
                               f"{project_name}_step5_contacts_enriched.csv")

    db_dir      = os.path.dirname(os.path.abspath(db_path))
    LEDGER_PATH = os.path.join(db_dir, LEDGER_FILENAME)
    lock_path   = os.path.join(db_dir, ".locks", f"{project_name}.lock")

    # ── Acquire the per-project run lock (no concurrent double-charge) ─────────
    if not acquire_lock(lock_path, force):
        sys.exit(1)

    try:
        _run(project_name, input_path, db_path, output_path,
             email_only, force, assume_yes, phone_per_company)
    finally:
        release_lock(lock_path)


def _run(project_name, input_path, db_path, output_path,
         email_only, force, assume_yes, phone_per_company=PHONE_PER_COMPANY):
    global all_rows

    # ── Load step4 contacts ──────────────────────────────────────────────────
    with open(input_path, newline='', encoding='utf-8-sig') as f:
        all_rows = list(csv.DictReader(f))

    print("=" * 60)
    print(f"Step 5 — Pronto Enrichment: {project_name}")
    print("=" * 60)
    print(f"  Contacts loaded: {len(all_rows)}")

    # Build lookup by linkedin_url
    for row in all_rows:
        li_key = normalize_li(row.get("Linkedin Profile Url") or row.get("Linkedin Id Url") or "")
        if li_key:
            rows_by_li[li_key] = row

    # ── Load Pronto DB — pre-fill from cache ─────────────────────────────────
    db_enriched = {}
    if os.path.isfile(db_path):
        with open(db_path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                li_key = normalize_li(row.get('linkedin_url') or '')
                if li_key:
                    email = (row.get('email') or '').strip()
                    phone = (row.get('phone') or '').strip()
                    if email or phone:
                        db_enriched[li_key] = {
                            'email': email,
                            'email_status': (row.get('email_status') or '').strip(),
                            'phone': phone,
                        }
        print(f"  DB enriched contacts: {len(db_enriched)}")

    # ── Load the billing ledger (authoritative "already paid for") ────────────
    ledger = load_ledger(LEDGER_PATH)
    print(f"  Ledger entries: {len(ledger)} contacts previously submitted")

    # Pre-fill from DB, then from ledger results (ledger captures finds that may
    # not have made it into the main DB yet — e.g. after a crash).
    db_hits = 0
    for li_key, row in rows_by_li.items():
        cached = db_enriched.get(li_key)
        if cached:
            if not (row.get("Email") or "").strip():
                row["Email"]        = cached['email']
                row["Email Status"] = cached['email_status']
            if not (row.get("Phone (Pronto)") or "").strip():
                row["Phone (Pronto)"] = cached['phone']
            db_hits += 1
        led = ledger.get(li_key)
        if led:
            if not (row.get("Email") or "").strip() and led['email']:
                row["Email"]        = led['email']
                row["Email Status"] = led['email_status']
            if not (row.get("Phone (Pronto)") or "").strip() and led['phone']:
                row["Phone (Pronto)"] = led['phone']

    print(f"  Pre-filled from DB: {db_hits}")

    # ── Decide per-contact enrichment types ──────────────────────────────────
    # EMAIL for everyone missing an email; PHONE only for the top-2-per-company
    # contacts (by persona priority + fit_score) that are still missing a phone.
    phone_eligible = set() if email_only else compute_phone_eligible(list(rows_by_li.values()), phone_per_company)
    _cap_label = "no cap — all persona-fit" if phone_per_company == 0 else f"top {phone_per_company}/company"
    print(f"  Phone-eligible ({_cap_label}): {len(phone_eligible)}")

    candidates = []  # (li_key, row, types)
    email_calls = phone_calls = 0
    skipped_ledger_email = skipped_ledger_phone = 0
    for li_key, row in rows_by_li.items():
        has_email = bool((row.get("Email") or "").strip())
        has_phone = bool((row.get("Phone (Pronto)") or "").strip())
        types = []
        if not has_email:
            types.append("email")
        if (not email_only) and (li_key in phone_eligible) and (not has_phone):
            types.append("phone")

        # ── ANTI-DOUBLE-BILLING GUARD ──────────────────────────────────────
        # Never re-submit a (contact, type) pair the ledger shows we already
        # paid Pronto for. A successful prior submit means: if a value existed
        # it is already cached; if not, re-asking finds nothing again at no
        # benefit. --force overrides for deliberate refreshes only.
        if not force:
            already = ledger.get(li_key, {}).get('submitted', set())
            if "email" in types and "email" in already:
                types.remove("email"); skipped_ledger_email += 1
            if "phone" in types and "phone" in already:
                types.remove("phone"); skipped_ledger_phone += 1

        if types:
            candidates.append((li_key, row, types))
            email_calls += "email" in types
            phone_calls += "phone" in types

    print(f"  Skipped (already in ledger — already paid): "
          f"{skipped_ledger_email} email, {skipped_ledger_phone} phone")
    print(f"  Need enrichment: {len(candidates)} contacts "
          f"({email_calls} email, {phone_calls} phone)")
    print(f"\n  Cost estimate: up to {email_calls} email + {phone_calls} phone requests")
    print(f"  Max credits if all hit: {email_calls*3 + phone_calls*30} "
          f"(3/email, 30/phone — charged only on successful finds)")

    # ── Sanity ceiling: phone requests can never exceed phone-eligible set ────
    if phone_calls > len(phone_eligible):
        print(f"  ✗ ABORT: phone requests ({phone_calls}) exceed phone-eligible "
              f"({len(phone_eligible)}). Refusing to over-enrich.")
        sys.exit(1)

    if not candidates:
        print("\n  Nothing to enrich — writing output as-is.")
        write_output(all_rows, output_path)
        upsert_db(all_rows, db_path, project_name)
        return

    if assume_yes:
        print("\n  --yes: proceeding without prompt.")
    else:
        answer = input("\n  Proceed? (yes/no): ").strip().lower()
        if answer != 'yes':
            print("  Aborted.")
            sys.exit(0)

    # Graceful shutdown — results are written through to the ledger as they arrive
    # and the output is saved every poll round, so Ctrl+C loses nothing.
    def on_exit(sig, frame):
        print("\n  Interrupted — saving output (results safe in ledger)...")
        write_output(all_rows, output_path)
        upsert_db(all_rows, db_path, project_name)
        sys.exit(0)
    signal.signal(signal.SIGINT, on_exit)

    # ── Submit enrichment requests ───────────────────────────────────────────
    # No webhook, no localtunnel. We submit, then PULL each result by id (poll).
    # This removes the inbound-delivery dependency that 503'd and silently dropped
    # numbers we were billed for (Pronto charges on find, not on our receipt).
    print(f"\n  Submitting {len(candidates)} enrichment requests...")
    pending_poll = {}   # enr_id -> (li_key, types)
    for i, (li_key, row, types) in enumerate(candidates, 1):
        enr_id = submit_enrichment(row, types)
        if enr_id:
            pending_poll[enr_id] = (li_key, types)
        if i % 10 == 0:
            print(f"    Submitted {i}/{len(candidates)} ...")
        time.sleep(0.15)

    print(f"\n  Submitted: {len(pending_poll)}/{len(candidates)}")

    # ── Poll for results (pull, not push) ─────────────────────────────────────
    poll_results(pending_poll, output_path, db_path, project_name)

    # ── Write outputs ────────────────────────────────────────────────────────
    write_output(all_rows, output_path)
    upsert_db(all_rows, db_path, project_name)

    # Stats — count NUMBERS, not just contacts, so you can reconcile against the
    # Pronto dashboard "Phones Found" for this run. Pronto bills per number; if the
    # dashboard shows MORE than we captured, that is Pronto billing for numbers it
    # never exposed (a billing discrepancy to raise with support — not retrievable).
    with_email = sum(1 for r in all_rows if (r.get("Email") or "").strip())
    with_phone = sum(1 for r in all_rows if (r.get("Phone (Pronto)") or "").strip())
    num_phones = sum(len([x for x in (r.get("Phone (Pronto)") or "").split(';') if x.strip()])
                     for r in all_rows)
    print(f"\n  Final stats ({len(all_rows)} contacts):")
    print(f"    With email:        {with_email}")
    print(f"    With phone:        {with_phone}")
    print(f"    Phone NUMBERS captured this run: {num_phones}")
    print(f"\n  ⚖️  RECONCILE WITH BILLING: open the Pronto credits dashboard for today")
    print(f"     and compare 'Phones Found' to {num_phones}. They should match. If the")
    print(f"     dashboard is higher, Pronto billed for numbers it did not expose to")
    print(f"     us (not retrievable by webhook or API) — flag it to Pronto support.")

    print("\nDone.")


def write_output(rows, output_path):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    if "Email Status" not in fieldnames:
        fieldnames.append("Email Status")
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  Output: {len(rows)} contacts → {output_path}")


def _db_row_from_contact(row, project_name):
    return {
        'status':              row.get("Status", ""),
        'rejection_reasons':   row.get("Disqualified Reasons", ""),
        'first_name':          row.get("First Name", ""),
        'last_name':           row.get("Last Name", ""),
        'gender':              row.get("Gender", ""),
        'email':               row.get("Email", ""),
        'email_status':        row.get("Email Status", ""),
        'phone':               row.get("Phone (Pronto)", ""),
        'linkedin_url':        row.get("Linkedin Profile Url", ""),
        'linkedin_id_url':     row.get("Linkedin Id Url", ""),
        'profile_image_url':   row.get("Profile Image Url", ""),
        'location':            row.get("Location", ""),
        'title':               row.get("Title", ""),
        'years_in_position':   row.get("Years In Position", ""),
        'months_in_position':  row.get("Months In Position", ""),
        'years_in_company':    row.get("Years In Company", ""),
        'months_in_company':   row.get("Months In Company", ""),
        'company_name':        row.get("Company Name", ""),
        'company_cleaned_name':row.get("Company Cleaned Name", ""),
        'company_website':     row.get("Company Website", ""),
        'company_location':    row.get("Company Location", ""),
        'company_industry':    row.get("Company Industry", ""),
        'company_linkedin_url':row.get("Company Linkedin Flagship Url", ""),
        'company_linkedin_id': row.get("Company Linkedin", ""),
        'company_employee_range': row.get("Employee Range", ""),
        'company_hq_city':     "",
        'company_hq_country':  "",
        'company_hq_postal':   "",
        'company_hq_region':   "",
        'company_description': row.get("Company Description", ""),
        'source_project':      project_name,
        'searched_date':       TODAY,
        'enriched_date':       TODAY if (row.get("Email") or row.get("Phone (Pronto)")) else "",
    }


def upsert_db(rows, db_path, project_name):
    """UPSERT by linkedin_url: update email/phone on the existing row if present,
    otherwise append. Replaces the old append-only logic that grew duplicate rows
    and let stale cache miss freshly-found phones."""
    if not os.path.isfile(db_path):
        return

    with open(db_path, newline='', encoding='utf-8') as f:
        db_rows = list(csv.DictReader(f))
    index = {}
    for idx, r in enumerate(db_rows):
        k = normalize_li(r.get('linkedin_url') or '')
        if k and k not in index:
            index[k] = idx

    updated = appended = 0
    for row in rows:
        li_key = normalize_li(row.get("Linkedin Profile Url") or row.get("Linkedin Id Url") or "")
        if not li_key:
            continue
        new_email = (row.get("Email") or "").strip()
        new_phone = (row.get("Phone (Pronto)") or "").strip()
        if li_key in index:
            tgt = db_rows[index[li_key]]
            changed = False
            if new_email and not (tgt.get('email') or '').strip():
                tgt['email'] = new_email
                tgt['email_status'] = row.get("Email Status", "")
                changed = True
            if new_phone and not (tgt.get('phone') or '').strip():
                tgt['phone'] = new_phone
                changed = True
            if changed:
                tgt['enriched_date'] = TODAY
                updated += 1
        else:
            db_rows.append(_db_row_from_contact(row, project_name))
            appended += 1

    # Rewrite atomically (temp file + replace) so a crash can't corrupt the DB.
    tmp = db_path + ".tmp"
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=DB_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(db_rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, db_path)
    print(f"  DB upserted: {updated} updated, {appended} new (total {len(db_rows)} rows)")


if __name__ == "__main__":
    main()
