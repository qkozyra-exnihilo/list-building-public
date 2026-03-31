#!/usr/bin/env python3
"""
step5_pronto_enrichment.py — Pronto Email + Phone Enrichment
=============================================================
Input:  {project}_step4_contacts_filtered.csv
Output: {project}_step5_contacts_enriched.csv (single output)

Logic:
  1. Load step4 contacts
  2. Check Pronto DB — pre-fill from cache, skip if already enriched
  3. Submit remaining candidates via Pronto API (async webhook)
  4. Receive webhook callbacks
  5. Write single output with enriched data
  6. Append new contacts to pronto_contacts_database.csv

Usage:
    python3 step5_pronto_enrichment.py <project_name> \\
        --input <step4_contacts_filtered.csv> \\
        [--db <pronto_contacts_database.csv>]
"""

import csv, json, os, re, signal, subprocess, sys, threading, time, warnings
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import date

warnings.filterwarnings('ignore')

PRONTO_API_KEY = os.environ["PRONTO_API_KEY"]
PRONTO_BASE    = "https://app.prontohq.com"
WEBHOOK_PORT   = 8765
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

api_headers = {
    "X-API-Key": PRONTO_API_KEY,
    "Content-Type": "application/json"
}


def normalize_li(url):
    return (url or '').strip().rstrip('/').lower()


# ── Global state ──────────────────────────────────────────────────────────────
rows_by_li   = {}
pending      = {}
received     = 0
lock         = threading.Lock()
all_rows     = []


# ── Webhook handler ──────────────────────────────────────────────────────────
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global received
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()

        try:
            data = json.loads(body)
        except Exception:
            return

        enrichment_id = data.get('id') or data.get('enrichment_id')
        email         = (data.get('email') or '').strip()
        email_status  = (data.get('email_status') or '').strip()
        phones        = data.get('phone') or data.get('phones') or []
        if isinstance(phones, str):
            phones = [phones]
        phone_str = '; '.join(p for p in phones if p)

        with lock:
            li_key = pending.get(enrichment_id)
            if li_key and li_key in rows_by_li:
                row = rows_by_li[li_key]
                if email:
                    row["Email"]        = email
                    row["Email Status"] = email_status
                if phone_str and not (row.get("Phone (Pronto)") or "").strip():
                    row["Phone (Pronto)"] = phone_str
                received += 1
                name = f"{row.get('First Name','')} {row.get('Last Name','')}".strip()
                print(f"  ✓ [{received}] {name}: email={email or '—'} phone={phone_str or '—'}")
            else:
                print(f"  ? Unmatched webhook id={enrichment_id}")

    def log_message(self, format, *args):
        pass


# ── Submit enrichment ────────────────────────────────────────────────────────
def submit_enrichment(row, webhook_url):
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
        "enrichment_type": ["email", "phone"],
        "webhook_url":     webhook_url,
    }
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
            return resp.get('id') or resp.get('enrichment_id') or resp.get('contact', {}).get('id')
        else:
            print(f"  ✗ Submit error {r.status_code}: {r.text[:150]}")
            return None
    except Exception as e:
        print(f"  ✗ Submit exception: {e}")
        return None


def main():
    global all_rows

    # ── Parse args ────────────────────────────────────────────────────────────
    if len(sys.argv) < 2:
        print("Usage: python3 step5_pronto_enrichment.py <project_name> "
              "--input <file> [--db <pronto_db.csv>]")
        sys.exit(1)

    project_name = sys.argv[1]
    input_path = None
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "database", "pronto_contacts_database.csv")

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '--input' and i + 1 < len(args):
            input_path = args[i + 1]
            i += 2
        elif args[i] == '--db' and i + 1 < len(args):
            db_path = args[i + 1]
            i += 2
        else:
            i += 1

    if not input_path:
        print("Error: --input is required.")
        sys.exit(1)

    output_path = os.path.join(os.path.dirname(os.path.abspath(input_path)),
                               f"{project_name}_step5_contacts_enriched.csv")

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

    # Pre-fill from DB
    db_hits = 0
    for li_key, row in rows_by_li.items():
        if li_key in db_enriched:
            cached = db_enriched[li_key]
            if not (row.get("Email") or "").strip():
                row["Email"]        = cached['email']
                row["Email Status"] = cached['email_status']
            if not (row.get("Phone (Pronto)") or "").strip():
                row["Phone (Pronto)"] = cached['phone']
            db_hits += 1

    print(f"  Pre-filled from DB: {db_hits}")

    # Candidates = no email AND no phone after DB pre-fill
    candidates = []
    for li_key, row in rows_by_li.items():
        has_email = bool((row.get("Email") or "").strip())
        has_phone = bool((row.get("Phone (Pronto)") or "").strip())
        if not has_email and not has_phone:
            candidates.append((li_key, row))

    print(f"  Need enrichment: {len(candidates)}")
    print(f"\n  Cost estimate: {len(candidates)} contacts")
    print(f"  Credits only consumed on successful finds (3/email, 30/phone)")

    if not candidates:
        print("\n  Nothing to enrich — writing output as-is.")
        write_output(all_rows, output_path)
        append_to_db(all_rows, db_enriched, db_path, project_name)
        return

    answer = input("\n  Proceed? (yes/no): ").strip().lower()
    if answer != 'yes':
        print("  Aborted.")
        sys.exit(0)

    # ── Start localtunnel ────────────────────────────────────────────────────
    print(f"\n  Starting localtunnel on port {WEBHOOK_PORT} ...")
    lt_proc = subprocess.Popen(
        ['npx', '--yes', 'localtunnel', '--port', str(WEBHOOK_PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )

    tunnel_url = None
    for _ in range(60):
        line = lt_proc.stdout.readline()
        if not line:
            time.sleep(0.5)
            continue
        print(f"    lt: {line.rstrip()}")
        m = re.search(r'https://[^\s]+\.loca\.lt', line)
        if m:
            tunnel_url = m.group(0)
            break

    if not tunnel_url:
        print("  Failed to get localtunnel URL — aborting.")
        lt_proc.terminate()
        sys.exit(1)

    webhook_url = f"{tunnel_url}/webhook"
    print(f"  Webhook URL: {webhook_url}")

    # Start local webhook server
    server = HTTPServer(('', WEBHOOK_PORT), WebhookHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    # Graceful shutdown
    def on_exit(sig, frame):
        print("\n  Interrupted — saving output...")
        write_output(all_rows, output_path)
        append_to_db(all_rows, db_enriched, db_path, project_name)
        lt_proc.terminate()
        sys.exit(0)
    signal.signal(signal.SIGINT, on_exit)

    # ── Submit enrichment requests ───────────────────────────────────────────
    print(f"\n  Submitting {len(candidates)} enrichment requests...")
    submitted = 0
    for i, (li_key, row) in enumerate(candidates, 1):
        enr_id = submit_enrichment(row, webhook_url)
        if enr_id:
            with lock:
                pending[enr_id] = li_key
            submitted += 1
        if i % 10 == 0:
            print(f"    Submitted {i}/{len(candidates)} ...")
        time.sleep(0.15)

    print(f"\n  Submitted: {submitted}/{len(candidates)}")
    print(f"  Waiting for callbacks (Ctrl+C to stop early and save)...")

    # Wait for callbacks
    last_received = received
    idle_seconds  = 0
    while received < submitted and idle_seconds < 300:
        time.sleep(5)
        if received > last_received:
            last_received = received
            idle_seconds  = 0
        else:
            idle_seconds += 5

    print(f"\n  Received {received}/{submitted} callbacks.")

    # ── Write outputs ────────────────────────────────────────────────────────
    write_output(all_rows, output_path)
    append_to_db(all_rows, db_enriched, db_path, project_name)

    # Stats
    with_email = sum(1 for r in all_rows if (r.get("Email") or "").strip())
    with_phone = sum(1 for r in all_rows if (r.get("Phone (Pronto)") or "").strip())
    print(f"\n  Final stats ({len(all_rows)} contacts):")
    print(f"    With email: {with_email}")
    print(f"    With phone: {with_phone}")

    lt_proc.terminate()
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


def append_to_db(rows, db_enriched, db_path, project_name):
    new_for_db = []
    for row in rows:
        li_key = normalize_li(row.get("Linkedin Profile Url") or row.get("Linkedin Id Url") or "")
        if not li_key or li_key in db_enriched:
            continue
        db_row = {
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
        new_for_db.append(db_row)

    if new_for_db and os.path.isfile(db_path):
        with open(db_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=DB_FIELDNAMES, extrasaction='ignore')
            writer.writerows(new_for_db)
        print(f"  DB updated: {len(new_for_db)} new contacts appended")


if __name__ == "__main__":
    main()
