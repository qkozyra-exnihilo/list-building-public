# B2B List Building Process

Last updated: 2026-04-21

## Goal
Build a high quality B2B contact list from any company list, enriched with relevant contacts, emails, and phone numbers.

## ICP: Company Filter
- Target: B2B SaaS companies (any vertical). Vertical SaaS counts.
- All rules in one file: see [company_search_filter_rules.md](company_search_filter_rules.md)
- Two gates in sequence:
    1. **Step 1d (primary)**: LLM homepage classifier → verdict `saas` / `not_saas` / `unclear` with confidence
    2. **Step 2 (safety net)**: NAICS exclusion-only + keyword rescue for vertical SaaS

## Input
- A list of companies with: company name + domain (LinkedIn URL optional)
- Sectors to exclude can be specified upfront

## Target Contacts (Priority Order) — reprioritized 2026-06; Sales dropped 2026-06-16
1. Finance (CFO / VP / Head / Director Finance, FP&A, DAF) — all levels; Controllers excluded
2. Revenue (CRO / CCO / Head / VP Revenue + Revenue Operations / RevOps) — all levels
3. Ops — Head-of+ only: COO / general Operations + Sales Ops + Growth Ops + Business Ops
   Founders / CEO / President / GM are NOT a target persona (dropped at any company size).
   Pure Sales (Head of Sales / VP Sales / Sales Director / CSO / Directeur Commercial) is
   ALSO NOT a target persona (dropped 2026-06-16). NB: Sales Operations stays under Ops,
   and CCO / Chief Commercial Officer stays under Revenue.

## Title Filters
- All rules in one file: see [contact_title_rules.md](contact_title_rules.md)
  (Sales Nav copy paste filters + Step 4 refinement inclusion/exclusion lists)
- Exclusions: board, investor, advisor, project director, back office, devops,
  marketing operations, IT operations, founders/CEO/President/GM, pure sales,
  chief of staff, managing director, treasurer.
  (NB: sales operations & business operations are now KEPT at Head-of+; revenue
  operations is kept at ALL levels under Revenue.)

---

## Step by Step Process

### Step 1: Company List
- Script: process/step1_company_list.py
- Usage:
    ```
    python3 step1_company_list.py <project_name> \
        --input <seed.csv> \
        --column-map name=company_name,domain=domain \
        [--extra-cols competitor_of,pricing_model,pricing_page_url]
    ```
- Input: list of companies with name + domain (CSV)
- Clean up: remove irrelevant sectors if needed
- Enrich with industry classification codes if available:
    - NAICS (North American Industry Classification System): preferred for US/global companies
    - NACE (Nomenclature des Activités Économiques): preferred for European/French companies
    - Add whichever code is findable; skip if neither is available
- See: [company_list_enrichment_rules.md](company_list_enrichment_rules.md) for details
- Output file: `{project_name}_step1_companies.csv`
    Columns: company_name, domain, sector (opt), nace_code (opt), naics_code (opt)

### Step 1b: Company List Cleaning (before enrichment)
- Script: process/step1b_company_cleaning.py
- Usage:
    ```
    python3 step1b_company_cleaning.py <project_name> \
        --input <step1_companies.csv>
    ```
- Input: `{project_name}_step1_companies.csv`
- Run before Apollo. Bad data wastes credits
- Cleaning rules:
    1. **Deduplicate by domain**: keep first occurrence, remove rows with the same domain
       (different company name variants pointing to the same domain count as duplicates)
    2. **Fix malformed domains**: strip common subdomains from the front:
       mail., www., app., m., web., go.
       Example: mail.joinsecret.com becomes joinsecret.com
    3. **Flag missing domains**: add a domain_flag = "MISSING_DOMAIN" column for companies
       with no domain; do NOT send these to Apollo (cannot enrich without domain)
- Report all changes: duplicates removed, domains fixed, companies flagged
- Output file: `{project_name}_step1b_companies_cleaned.csv`
    Same columns as step1 + domain_flag column

### Step 1c: Domain & LinkedIn Verification (AI assisted)
- Script: process/step1c_domain_verification.py
- Usage:
    ```
    python3 step1c_domain_verification.py <project_name> \
        --input <step1b_or_step2.csv>
    # To apply a manually edited corrections file:
    python3 step1c_domain_verification.py <project_name> \
        --input <step1b_or_step2.csv> \
        --corrections <step1c_verification.csv>
    ```
- **CHECKPOINT: this step MUST run after Step 2 if any companies have no linkedin_company_url.** Do not proceed to Step 2b until all fixable companies have been resolved.
- Input: `{project_name}_step1b_companies_cleaned.csv` (pre Apollo) OR `{project_name}_step2_companies_with_linkedin.csv` (post Apollo, for companies with no LinkedIn URL)
- Goal: catch wrong or outdated domains AND find missing LinkedIn URLs
- Trigger this step for companies that:
    - Have domain_flag = "MISSING_DOMAIN"
    - Apollo returned no result (no linkedin_company_url after Step 2 attempt)
    - Domain looks suspicious (wrong TLD, subdomain, redirect, old brand name)
- Method: use AI web search to find correct primary domain + LinkedIn URL for each flagged company
    - Search: "{company_name} official website"
    - Search: "{company_name} LinkedIn company page"
    - Check for rebrands (e.g. Alloreview becomes AlloBrain)
    - Check for domain redirects (e.g. dotblocks.fr becomes dotblocks.com)
    - Check for wrong domains in source list (e.g. capgemini-consulting.com for Choosemycompany)
- Output: a correction table with for each company:
    - company_name, current_domain, correct_domain, linkedin_url, notes
- Apply corrections to the cleaned file before re-running Apollo
- Only run AI search for companies with issues. Do NOT re-check all 300+ companies
- Output file: `{project_name}_step1c_companies_verified.csv`
    Same columns as step1b with corrected domains applied

### Step 1c Live: LinkedIn URL HEAD Check (recommended after Step 2)
- Script: process/step1c_linkedin_live_check.py
- Usage:
    ```
    python3 step1c_linkedin_live_check.py <project_name> \
        --input <step1c_companies_verified.csv> \
        [--workers 5] [--rate-limit 1.0]
    ```
- Purpose: catch the silent slug-drift cases that Apollo caches forever
- Detects:
    - **404**: slug is dead on LinkedIn (e.g. Javelo / Foederis / Coorpacademy / Tinyclues — companies that rebranded or were acquired and Apollo never refreshed). Pronto can't resolve these → 0 Pronto leads.
    - **Redirect**: slug exists but redirects to a different one (e.g. Platform.sh → upsundotcom rebrand). Use the redirect target as the real slug.
    - **200 same slug**: passes. Note: does NOT catch zombie pages like Skeepers `s-keeper` which return 200 but are wrong-entity. Step 2c's low-employee-count check is the complement.
- Output file: `{project_name}_step1c_linkedin_check.csv` (step1c-compatible)
- Apply via:
    ```
    python3 step1c_domain_verification.py <project_name> \
        --input <step2_companies_with_linkedin.csv> \
        --corrections {project_name}_step1c_linkedin_check.csv
    ```
- LinkedIn rate-limits aggressively. Default 5 workers + 1s rate limit is conservative; bump cautiously.

### Step 1d: ICP Check (research-then-analyze — primary gate)
- Module: `icp-research/icp_v2.py` (**replaces** the old `process/step1d_icp_homepage_check.py` homepage-only classifier, which flipped ~5% of borderline verdicts run-to-run — kept in the repo only for reference/back-compat, do not use for new runs).
- Full rule + rationale: `process/company_search_filter_rules.md` (§ Step 1d). Summary below.
- Requires: `ANTHROPIC_API_KEY`, `SERPER_API_KEY` env vars (Attio fields no longer used as inputs); `anthropic` + `requests`.
- Usage (two idempotent phases over `icp_context.db`):
    ```
    python3 icp_v2.py research   # crawl homepage + product/pricing/about/customers + web-search snippet → context table
    python3 icp_v2.py analyze    # one structured call per company over stored context → verdict table
    python3 icp_v2.py both       # both
    ```
    Env knobs: `ICP_SRC` (input CSV), `ICP_MODEL`, `ICP_THRESHOLD` (default 0.80), `ICP_RESEARCH_WORKERS`, `ICP_ANALYZE_WORKERS`.
- **Always run this step.** It is the primary ICP gate for the pipeline.
- **Target (revised 2026-07-01):** bills **recurring/usage revenue** (SaaS, usage/API, marketplaces w/ take-rate, fintech/insurtech, hardware+recurring-service) — broadened from the old "is it SaaS". Pricing-page absence is **neutral** (contact-sales enterprises have none).
- **Two dimensions:** Dim 1 revenue model (this step, **LIVE**) + Dim 2 sales motion. Dim 2 can't be judged from a web crawl (no headcount), so it is enforced later at **Step 2a** off the Apollo `departmental_head_count` (sales+BD ≥ 2) — see Step 2a. The "hiring sales" half of the original OR-definition is not implemented.
- Decision rules (threshold canonical at **0.80**):
    - `saas` with confidence ≥ 0.80 → **KEEP** (in ICP)
    - `not_saas` with confidence ≥ 0.80 → **DROP** (Not ICP)
    - anything else (`unclear`, low confidence) → **UNCERTAIN / KEEP FOR REVIEW**
- Cost: ~$30 / 6.2k companies on Haiku 4.5 for a full pass; research phase is cached so re-scoring is cheap.
- Output: verdicts in `icp_context.db` (`verdict` table); export via `icp_database.csv` / dashboards (`all4_results.html`, `db_overview.html`).

#### Step 1d → 1e bridge (`icp_export.py`)
The verdicts live in the DB, but downstream Step 1e still consumes the old per-project file contract. `icp-research/icp_export.py` joins a project's company list against the stored verdicts and writes the exact two files Step 1e expects:
```
python3 icp_export.py <project_name> \
    --input <companies.csv> \
    --domain-col "Domains"        # or "Company Website" / "domain" per your list
    [--threshold 0.80] [--out-dir <dir>]
```
- Applies the canonical 0.80 decision rule (`saas≥0.80`=keep, `not_saas≥0.80`=drop, else review/keep).
- Writes `{project}_step1d_classification.csv` (domain, company_name, classification, vertical, confidence, reason, source), `{project}_step1d_icp_filtered.csv` (input rows minus the DROPs, original columns preserved — feeds Step 1e directly), and `{project}_step1d_review.html` — a self-contained review artifact (summary cards + filterable/sortable table, DROP rows surfaced first with the verdict reason) to **eyeball the filtering before proceeding**. Suppress with `--no-html`.
- Companies with **no verdict in the DB are never dropped** (kept as `unreachable`/review) and reported as a WARNING. If a project has uncovered companies, run `ICP_SRC=<list> python3 icp_v2.py both` on that list first (needs `Record ID` + `Domains` columns), then re-export.

### Step 1e: Pricing Page Detection
- Script: process/step1e_detect_pricing_pages.py
- Usage:
    ```
    python3 step1e_detect_pricing_pages.py <project_name> \
        --input <step1d_icp_filtered.csv> \
        --domain-col domain
    ```
- Input: `{project_name}_step1d_icp_filtered.csv`
- Method: 3-step pipeline per domain (stops at first match):
    1. **Common URL paths**: /pricing, /plans, /tarifs, /prices, etc.
    2. **Homepage link scan**: search for pricing keywords in `<a>` tags
    3. **Sitemap scan**: check /sitemap.xml for pricing URLs
- Filters out false positives (blog posts, affiliate links, corporate pages)
- Output file: `{project_name}_step1e_pricing_detected.csv`
    Same columns as input + pricing_page_url, pricing_detected_by

### Step 1f: Pricing Page Validation
- Script: process/step1f_validate_pricing_pages.py
- Usage:
    ```
    python3 step1f_validate_pricing_pages.py <project_name> \
        --input <step1e_pricing_detected.csv>
    ```
- Input: `{project_name}_step1e_pricing_detected.csv`
- Fetches each detected pricing page and checks for real pricing signals:
    - Currency symbols ($, EUR, £)
    - Pricing terms (per month, per user, /mo, /yr, annually, monthly)
    - Plan names (free, starter, pro, enterprise, basic, premium, business)
    - Price patterns (e.g. 29.99, 199.00)
- Scoring: score >= 5 = valid pricing page
- Output file: `{project_name}_step1f_pricing_validated.csv`
    Same columns as input + valid_pricing_page, pricing_score, pricing_description

### Step 1g: Cross-source reuse check (MANDATORY before any paid enrichment)
- Script: process/step1g_db_reuse_check.py
- **Why**: before spending Apollo credits (Step 2) or Pronto credits (Step 5), check
  EVERY source we already own. Do not re-pay for companies/contacts we already have,
  and do not cold-prospect companies that are already customers / in active pipeline.
- **Check ALL of these — not just the local Apollo CSV**:
    1. `database/apollo_companies_database.csv` — company firmographics + LinkedIn URL (Step 2 cache)
    2. `database/pronto_contacts_database.csv` — already-enriched contacts (email + phone we own)
    3. **Supabase TAM** (`pricing-benchmark` `companies` table, ~2.9k companies) — LinkedIn URL,
       NAICS/industry, pricing model, and **tech stack (`current_billing_tool` / `current_payment_provider` / `current_crm`)**.
       Export the rows for this project's domains (run from `~/github/research/pricing-benchmark`):
       ```
       npx supabase db query --linked "SELECT domain, linkedin_url, current_billing_tool,
         current_payment_provider, current_crm, pricing_model FROM companies
         WHERE domain IN ('a.com','b.io', ...);" --output json   # → convert to CSV, pass as --tam-csv
       ```
    4. **Attio CRM** — via the Attio MCP (`search-records`, object=`companies`, one query per domain).
       Capture per domain: in_attio, status/lifecycle, account_owner, record_id.
       **Any company that is an existing customer or in an active deal MUST be excluded** from a
       cold-prospecting list. Save as a CSV (columns: domain, in_attio, attio_status, account_owner, record_id) and pass as `--attio-csv`.
- Usage:
    ```
    python3 step1g_db_reuse_check.py <project_name> \
        --input <step1f_or_step1b.csv> --domain-col domain \
        [--tam-csv <supabase_export.csv>] [--attio-csv <attio_export.csv>] \
        [--max-coverage]
    ```
- **Contact strategy** (per-run decision):
    - **standard** (default): a company with >= `--min-contacts` (default 2) valid-persona
      contacts already in the Pronto DB is marked covered → skip the Pronto search for it.
    - **`--max-coverage`**: NEVER skip. Run Pronto on every company to pull as many
      valid-persona contacts as possible; DB contacts are reused for free and Pronto
      results are deduped against them (no re-billing). Use when the ask is "enrich as
      many contacts as possible", not "at least N".
- Output: `{project}_step1g_reuse_check.csv` (per-company: linkedin_source, valid_db_contacts,
  needs_apollo, needs_pronto, in_attio, attio_status, attio_exclude, tam_crm, tam_billing_tool)
  + a funnel summary. Review this BEFORE Step 2.

### Step 2: Apollo Enrichment (Company LinkedIn URLs)
- Script: process/step2_apollo_enrichment.py
- **Run Step 1g first** — only companies with `needs_apollo=yes` (no LinkedIn URL in any
  source) should reach the Apollo API; the rest reuse their DB/TAM LinkedIn URL for free.
- Usage:
    ```
    python3 step2_apollo_enrichment.py <project_name> \
        --input <step1b_or_step1c.csv> \
        [--skip-icp-filter] \
        [--max-employees 500]   # ICP size cap (default 500; 0 disables)
    ```
- Tool: Apollo API (/api/v1/organizations/bulk_enrich)
- API Key: env var APOLLO_API_KEY
- Input: `{project_name}_step1c_companies_verified.csv`
- Note: Use header "X-Api-Key" (not body). Add Mozilla User-Agent to avoid Cloudflare block.
- Skip companies with domain_flag = "MISSING_DOMAIN". Apollo cannot enrich without a domain
- Apollo charges credits per API call. Check the database FIRST:
    1. Load database/apollo_companies_database.csv
    2. Match by domain (case insensitive)
    3. If a row exists, reuse linkedin_company_url from the database, skip Apollo
    4. Only send companies NOT found in the database to the API
- Before launching enrichment, estimate cost and ask for approval:
    - Count how many companies are missing from the database (and have a valid domain)
    - Estimate cost: N companies x price per credit
    - Show the estimate to the user and wait for explicit confirmation
    - Do NOT start enrichment without cost approval
- After enrichment: append new results to database/apollo_companies_database.csv (never overwrite)
- Back fill from DB: for each company matched in the DB, populate:
    - industry: from DB `industry` field (primary industry label from Apollo)
    - naics_code: DB stores naics_codes as a list, take first value
    - naics_label: derive from the code (5 digit first, fall back to 2 digit sector)
    - keywords: from DB `keywords` field (used for ICP keyword confirmation)
    - Only fill if the field is currently empty (never overwrite manually set values)
- **ICP filter (NAICS exclusion + keyword rescue):** after enrichment,
  apply the safety-net filter defined in [company_search_filter_rules.md](company_search_filter_rules.md).
  1. Drop if `naics_code` starts with an EXCLUDED prefix (restaurants, retail, construction,
     funeral services, etc.) AND no positive SaaS keyword rescues it
  2. Drop if keywords are "consulting"-only (no positive SaaS signal)
  3. Drop if keywords are all on the exclude list (food, farming, maritime, etc.)
  4. Otherwise keep.
  This is a narrow safety net — the primary ICP gate is Step 1d. Vertical SaaS
  whose NAICS reflects their customer industry (fintech, healthtech, proptech…)
  is rescued by having SaaS-signal keywords in Apollo.
  Report: N kept, N dropped (NAICS), N dropped (keywords), N dropped (size).
- **Company-size gate:** drops companies above `--max-employees` (default **500**)
  using Apollo's `estimated_num_employees`. Companies with an unknown count are
  kept. Hyperline's outbound ICP is SMB/scaleup; >500-emp companies are enterprise
  and also flood the Pronto results with finance contacts. See
  [company_search_filter_rules.md](company_search_filter_rules.md).
- Output file: `{project_name}_step2_companies_with_linkedin.csv`
    Columns: all step1c columns + linkedin_company_url + industry + naics_code + naics_label
    Only includes companies passing ICP filter (industry/NAICS + keyword confirmation)

### Step 2a: Sales-Motion Gate (added 2026-07-02)
- Script: process/step2a_sales_motion_filter.py
- Usage:
    ```
    python3 step2a_sales_motion_filter.py <project_name> \
        --input <step2_companies.csv> \
        [--min 1]        # min sales+BD headcount to pass (default 1)
        [--flag-only]    # keep everything, only tag the verdict (no drops)
    ```
- Input: `{project_name}_step2_companies_with_linkedin.csv`
- **This is the enforcement of Step 1d's "Dimension 2 — sales motion".** It was
  designed at Step 1d but never enforceable there (a web crawl has no reliable
  headcount). Apollo returns a per-department headcount, so the gate lives here:
  right after Apollo, **before** any paid Pronto per-contact work (Steps 3 & 5),
  so we never spend search/enrichment credits on a company we'd cut.
- Rationale: Hyperline sells billing/revenue infrastructure. A real commercial
  motion (a sales team, negotiated deals, contracts) is the complex-billing buyer
  Hyperline serves; a company with no sales motion (pure self-serve micro-SaaS,
  founder-only, non-commercial) is a weaker fit and floods Pronto with noise.
- Logic (Apollo-only, **hard-drop** — decided 2026-07-02):
  join by domain to `apollo_companies_database.csv`, read `departmental_head_count`,
  compute `sales_motion_headcount = sales + business_development`.
    - `>= --min` (default **1** — lowered from 2 on user decision 2026-07-02: only
      companies with ZERO commercial staff are dropped) → KEEP (`sales_motion=strong`)
    - `0..min-1` WITH Apollo data present → DROP (`sales_motion=weak`)
    - Apollo data missing/unparseable → KEEP (`sales_motion=unknown`) — never drop
      on missing data, same rule as the ICP gate.
- `business_development` is counted alongside `sales` (BDR/outbound is a sales motion).
- The "hiring sales" half of the original OR-definition is **not** implemented
  (would need TheirStack job-postings, ~3 cr/company); Apollo current-headcount only.
- Report: N strong / N unknown (kept) / N weak (dropped).
- Output files:
    - `{project_name}_step2a_companies_sales_motion.csv` — KEPT companies (+ `sales_headcount`,
      `bd_headcount`, `sales_motion_headcount`, `sales_motion`, `sales_motion_reason`); feeds Step 2b.
    - `{project_name}_step2a_sales_motion_report.csv` — ALL companies + verdict (dropped first), auditable.

### Step 2b: DB Contact Quality Check
- Script: process/step2b_db_contact_check.py
- Usage:
    ```
    python3 step2b_db_contact_check.py <project_name> \
        --input <step2_companies.csv>
    ```
- Input: `{project_name}_step2_companies_with_linkedin.csv`
- For each company, check database/pronto_contacts_database.csv (match by company_linkedin_url):
    - Apply inclusion/exclusion rules (same as Step 4) to existing DB contacts
    - Count contacts that pass the filter (relevant title + not rejected)
- Decision logic:
    - >= 2 valid contacts in DB: skip Pronto search; pull those contacts into step2b_db_contacts.csv
    - 0 to 1 valid contacts in DB: include in Pronto import CSV (need fresh or additional search)
    - Not in DB at all: include in Pronto import CSV
- Output files:
    - `{project_name}_step2b_pronto_import.csv`: Pronto ready CSV for companies needing fresh search
        Columns: "Company Name", "Company Website", "LinkedIn URL", "LinkedIn ID"
        - Company Name = company_name from step2 output
        - Company Website = domain from step2 output
        - LinkedIn URL = linkedin_company_url from step2 output
        - LinkedIn ID = linkedin_uid from apollo_companies_database.csv (numeric ID, not slug)
    - `{project_name}_step2b_db_contacts.csv`: valid contacts pulled from DB (bypass Pronto search)
        Same columns as the Pronto export format; fed directly into Step 4 alongside fresh results
- **Coverage rule (2026-05-26)**: only Finance/RevOps/COO contacts count toward the "covered by DB ≥ 2" threshold (`is_primary_persona()`). Founders are pulled along as supplement but never block a Pronto search. Discovered via the Dust false-coverage bug.
- **linkedin_uid sanitization (2026-05-26)**: `sanitize_linkedin_uid()` drops Apollo 24-char org_ids (which Apollo sometimes stores instead of LinkedIn IDs) and falls back to the slug from the URL. Prevents Pronto resolution failures.

### Step 2c: Pronto Resolution Audit (recommended after Pronto upload)
- Script: process/step2c_pronto_resolution_audit.py
- Run AFTER uploading the step2b CSV to Pronto AND exporting the Pronto account list
- Usage:
    ```
    python3 step2c_pronto_resolution_audit.py <project_name> \
        --uploaded {project_name}_step2b_pronto_import.csv \
        --resolved <Pronto_company_export_*.csv>
    ```
- Purpose: catch slug issues BEFORE running the Sales Nav search (saves credits on wasted searches)
- Surfaces:
    - **Pattern 2 — Pronto failed to resolve**: empty enrichment fields (website/employee count/LinkedIn ID empty). Pronto echoes back what we sent without matching to any Sales Nav entity.
    - **Pattern 3 — Resolved to zombie entity**: low employee count (<15) on a company we know is bigger. Pronto matched to a stale/duplicate LinkedIn page with near-zero people.
    - **Missing from export**: Pronto silently dropped the company from the account list entirely.
    - **Country mismatch**: Apollo says France, Pronto says Latvia → wrong entity.
- For each flagged company: WebSearch real LinkedIn slug + patch `database/apollo_companies_database.csv` and the project's step1c verified file. Re-run step2b → re-upload to Pronto → re-export → re-audit until clean.
- The downstream zero-leads alarm in Step 4 catches any remaining cases by behavior.

### Qualification Artifact (after Step 2b, before Step 3)
- Script: process/qualification_artifact.py
- Usage:
    ```
    python3 qualification_artifact.py <project_name> [--title "Nice Title"]
    ```
- Generates `{project_name}_qualification_report.html`: the full funnel
  (seed → cleaning → ICP → Apollo/size → sales-motion → Pronto-ready), every
  dropped company with the gate that fired + reason, and the qualified list
  with ICP confidence / sales-team size / pricing-page status.
- **Always publish it as a Claude artifact** (standing request 2026-07-02) so the
  run is reviewed visually before spending Pronto credits. Favicon 🎯, stable.
- **Re-run + republish after Step 4** (standing request 2026-07-02): once
  `{project}_step4_contacts_filtered.csv` + `{project}_uncovered_companies.csv`
  exist, the report automatically gains a **Contact coverage** section splitting
  uncovered companies by cause — `zero_pronto_leads` (search returned nothing:
  slug issue or company too small) vs `leads_all_filtered` (leads exported but
  none passed the persona filter) — plus the kept-contact persona distribution.
- Design tokens follow the "ICP + sales motion" artifact family so all
  qualification reports read as one system.

### Step 3: Pronto Contact Search (MANUAL)
- Tool: Pronto UI + LinkedIn Sales Navigator
- Approach: all 4 filters combined: function + seniority + title keywords + geography
- Can be scoped to an account list OR run as a broad search (Step 4 filters later)

#### What you do (manual, 2 steps)
1. **Run a Sales Nav search** in Pronto UI with:
   - **Function:** Engineering, Accounting, Business Development, Entrepreneurship,
     Finance, Operations, Sales, Administrative
   - **Seniority level:** CXO, Director, Vice President, Strategic, Owner / Partner
   - **Geography:** France, United States, United Kingdom, Belgium, Switzerland,
     Luxembourg, Jersey
   - **Job Title inclusion keywords** (~80 keywords covering all target roles):
     See [contact_title_rules.md](contact_title_rules.md) for the full list to paste
   - **Job Title exclusion list** (paste in Sales Nav exclusion box):
     See [contact_title_rules.md](contact_title_rules.md) for the full list to paste
   - Optionally scope to an **Account List** (if uploaded in Step 2b)
2. **Export the raw leads CSV** from Pronto UI
   - Go to Leads, click the search, Export button (top right)
   - Pronto names the file: Pronto_lead_export_{search_name}_{DDMMYYYY}.csv
   - Drop the file in the project folder and tell me the filename
   - The Pronto "Leads" section has NO public API. Export is always manual
   - If not scoped to account list: export contains contacts from ALL companies.
     Step 4 cross references against your company list to keep only matches.

- Output file: `Pronto_lead_export_{search_name}_{DDMMYYYY}.csv`
    Raw Pronto export, all contacts before any filtering

### Step 4: Contact Refinement (hybrid LLM persona filter — keep ALL relevant)
- Script: process/step4_contact_refinement.py (+ process/persona_judge.py)
- Input: one or more Pronto export CSVs + optional DB contacts CSV
- Usage:
    ```
    python3 step4_contact_refinement.py <project_name> \
        --companies <step2_companies.csv> \
        --contacts <pronto_export1.csv> [<pronto_export2.csv> ...] \
        [--db-contacts <step2b_db_contacts.csv>] \
        [--no-llm]            # force deterministic fallback (offline / no API)
    ```
- Cross reference against company list (match by domain or Company LinkedIn URL)
- **Hybrid classification** (see [contact_title_rules.md](contact_title_rules.md)):
    - **Stage A** — deterministic noise removal (`HARD_DROP`): investors, board,
      advisors, VC/fund, students, interns, freelance/fractional/consultant, wrong
      C-suite (CTO/CPO/CMO/CIO/CDO/CoS). No model call.
    - **Stage B** — LLM persona judge (PRIMARY, `persona_judge.py`): everything that
      survives Stage A is judged by Claude → `{keep, category, seniority_tier,
      fit_score, reason}`. Catches title variants the keyword dict misses (Finance
      Lead, Finance Operations, Manager-Finance-EMEA). `ANTHROPIC_API_KEY` required.
    - Reproducible (temp 0 + cache `database/persona_judgments.csv`) and auditable
      (every verdict carries a `reason`). On per-contact API error → deterministic
      fallback so no contact is lost.
- **Priority (Finance > Revenue > Ops), level rules (reprioritized 2026-06; Sales dropped 2026-06-16):**
    1. Finance — ALL levels incl. junior (Lead, Operations, Manager, Analyst)
    2. Revenue — revenue leadership (CRO/CCO/Head/VP Revenue) + RevOps, ALL levels
    3. Ops — "Head of" and above only: COO/general ops + Sales Ops + Growth Ops + Business Ops
    - Founders / CEO / President / GM are NOT a persona — dropped at any company size.
      A combined title resolves by its qualifying function (e.g. "CEO & CFO" → Finance).
    - Pure Sales (Head of Sales / VP Sales / Sales Director / CSO / Directeur Commercial) is
      NOT a persona — dropped 2026-06-16. NB: Sales Operations stays under Ops (#3), and
      CCO / Chief Commercial Officer stays under Revenue (#2).
    - Overlap: Revenue Operations is classified as Revenue (#2, all levels), not Ops.
- **Keeps ALL relevant contacts — no per-company cap** (the cap moved to Step 5 phone).
- Output columns add: `_priority_category`, `_fit_score`, `_seniority_tier`,
  `_judge_reason`, `_judged_by` (used by Step 5 phone ranking).
- Output file: `{project_name}_step4_contacts_filtered.csv`

### Step 5: Pronto Enrichment (Email for all, Phone for top 2/company)
- Script: process/step5_pronto_enrichment.py
- Tool: Pronto API (/api/v2/contacts/single_enrich)
- Usage:
    ```
    python3 step5_pronto_enrichment.py <project_name> \
        --input <step4_contacts_filtered.csv> \
        [--db <pronto_contacts_database.csv>] \
        [--email-only]        # skip phone entirely (cheapest)
        [--force]             # DANGER: re-submit contacts already in the ledger (can re-bill)
        [--yes]               # skip the interactive confirm (for automation)
    ```
- Async: requires webhook server (Python HTTP server + localtunnel)
- **🔒 Anti-double-billing safeguards (added 2026-06-01 after a ~11K-credit
  over-enrichment — phones were re-found ~4× by re-running the same pass):**
    - **Run lock** — `database/.locks/<project>.lock`. Only one run per project at
      a time; a second concurrent run aborts (concurrency was the root cause).
    - **Billing ledger** — `database/pronto_enrichment_ledger.csv`, append-only.
      Every successful Pronto submit is logged the instant it is accepted (a charge
      becomes possible then). A `(linkedin_url, type)` pair already in the ledger is
      **never re-submitted** unless `--force`. Survives crashes/restarts. The cost
      estimate now prints "Skipped (already in ledger)" and max-credits.
    - **Write-through** — webhook callbacks append results to the ledger immediately,
      so a killed run never loses what it already paid for.
    - **Sanity ceiling** — aborts if phone requests would exceed the phone-eligible
      set (top-2/company), so a phone pass can never run on the wider pre-step4 set.
    - **DB upsert** — results are upserted by `linkedin_url` (update in place, no more
      duplicate rows), written atomically (temp file + rename).
    - The ledger was backfilled from the existing DB (≈1,900 contacts: ~1,875 email-,
      ~938 phone-protected) so already-enriched contacts can't be re-billed.
    - **Golden rule:** never pass `--force` unless you genuinely want to pay again
      for a months-stale refresh.
- **🔌 Result retrieval = POLLING, not webhook (rebuilt 2026-06-01):** step5 no
  longer runs a localtunnel + webhook server. It submits, then **polls
  `GET /api/v2/contacts/{id}`** for each enrichment id until `status:"finished"`,
  and **reconciles** (every submitted id must be retrieved → prints
  "billed == submitted == captured"). Why: Pronto charges per phone **number** on
  the *find* (not on our receipt), and our old webhook returned **503** under the
  ~100-callback burst, silently dropping numbers we'd paid for (Pronto reported the
  503s; the Rillet run lost data this way). Polling is pure outbound, has no inbound
  dependency, and can't 503. Output is saved every poll round so a crash loses nothing.
  - **Polling is NOT free / not cheaper** (verified 2026-06-01): retrieving a found
    number the first time charges the normal 30 (same as the webhook would have);
    only re-reading an already-delivered result is free. Its value is **reliability +
    reconciliation** — you receive, and can see, every number you pay for. There is
    no "free recovery" of lost numbers — pulling them IS the charge.
  - **Billing reality:** budget **~37 credits per delivered number / ~50+ per
    phone-contact**, NOT a flat 30/contact — contacts return 2–5 numbers, each 30 cr.
  - **Known Pronto discrepancy:** Pronto may bill for more numbers than its API
    returns (Rillet: 188 billed vs 151 exposed). Those extra numbers are NOT
    retrievable by any means — raise with Pronto support if the gap is large.
- **Per-contact enrichment types (cost control — phone ≈ 10× email):**
    - **Email** — enriched for EVERY contact missing an email.
    - **Phone** — enriched only for the top `PHONE_PER_COMPANY` (=2) contacts per
      company, ranked by persona priority + `_fit_score` from Step 4.
- Pronto credit pricing (credits only consumed on successful finds):
    - 1 email found = 3 credits
    - 1 phone number found = 30 credits
    - No charge if nothing found
- DB cache: auto checks database/pronto_contacts_database.csv by linkedin_url
    - Reuses cached email/phone; only calls for what's still missing (and, for
      phone, only if the contact is in the top-2-per-company set)
    - Appends new contacts to DB after enrichment
- Output file: `{project_name}_step5_contacts_enriched.csv`

### Step 6: Final Output
- Script: process/step6_final_output.py
- Usage:
    ```
    python3 step6_final_output.py <project_name> \
        --input <step5_contacts_enriched.csv> \
        [--companies <step2_companies.csv>] \
        [--phone-prefix +33]
    ```
- Optional features:
    - `--companies`: backfill Company Website from step2 company list (Pronto often leaves it blank)
    - `--phone-prefix`: filter phones by country prefix (e.g. +33 for France)
- Drops internal columns, keeps only useful outreach columns
- When --companies is provided, also computes company coverage:
    - Cross references step2 companies against contacts in FINAL
    - Outputs a list of uncovered companies (0 contacts) for potential iteration
- **IMPORTANT:** FINAL.csv column names MUST use Pronto-style headers (not snake_case). The attio_import.py script does case-sensitive column lookups.
- Output files:
    - `{project_name}_FINAL.csv`: final contacts deliverable, ready for Attio + Lemlist import
    - `{project_name}_uncovered_companies.csv`: companies with no contacts (for iteration)

#### Required FINAL.csv column headers (exact names)

| Column header | Description |
|---|---|
| First Name | Contact first name |
| Last Name | Contact last name |
| Email | Contact email |
| Email Status | Email validation status |
| Phone (Pronto) | Phone number |
| Title | Job title |
| Linkedin Profile Url | Contact LinkedIn URL |
| Location | Contact location (city, region, country) |
| Company Name | Company name |
| Company Website | Company domain |
| Company Linkedin Flagship Url | Company LinkedIn URL |
| Company Industry | Industry label |
| Employee Range | Employee count range |
| Company HQ City | Company headquarters city |
| Company HQ Country | Country code |
| Company Description | Company description |
| Year Founded | Year the company was founded |

Additional project specific columns (comment, competitor_of, pricing_model, etc.) can use any naming convention.

### Step 7: Attio Import (API based)
- Input: `{project_name}_FINAL.csv` (from Step 6)
- Script: attio/attio_import.py (generic, shared across all projects)
- API base: https://api.attio.com/v2
- API key: env var ATTIO_API_KEY
- Full import rules: attio/attio_import_rules.txt + attio/round_robin_logic.txt

#### No more intermediate CSVs
The old workflow generated companies.csv + contacts.csv via generate_import_csvs.py before
importing. The new script reads FINAL.csv directly, extracts company and contact data from
the same file, deduplicates companies by domain, and imports everything via the Attio API.
No CSV generation step needed.

#### Pre import checklist
1. Verify "Outbound" exists as an option in the Contact > source select field in Attio
2. Verify source_detail value: format "list-building_<descriptor>-<YY>"
3. Confirm record counts look right (unique domains for companies, total rows for contacts)
4. Ask user for explicit approval before starting. Show summary:
    - N companies to upsert (N new / N existing based on domain match)
    - N contacts to upsert (N new / N existing based on email match)
    - source_detail value that will be applied
    - Round robin pool assignments (francophone vs rest of world)

#### Import order
1. Companies first (match by domain)
2. Contacts second (match by email_addresses)

This ensures company references resolve correctly when contacts are imported.

#### How it works: 2 phase upsert per record

**Company upsert:**
- Phase 1: always overwrite (freshness fields):
    employee_range, estimated_number_of_employees, estimated_arr_usd,
    outbound_processed_at, funding_stage, funding_raised_usd, last_funding_date,
    sales_headcount, hiring_sales_date, hiring_sales_summary, hiring_sales_url
- Phase 2: fill if empty (static fields, only written if Attio field is currently blank):
    name, description, industry, founded_year, source_detail, linkedin,
    primary_location, account_owner
- NEVER write: linkedin (on update), tier, all internal custom fields, notes

**Contact upsert:**
- Phase 1: always overwrite (freshness fields):
    job_title, phone_numbers, primary_location, company (name or domain)
- Phase 2: fill if empty:
    name (first_name + last_name), linkedin, description, persona,
    lead_status, source, source_detail, contact_owner
- NEVER write: linkedin (on update), notes, tags (except additive)

**Owner assignment (fill if empty only):**
- account_owner: assigned via round robin ONLY if no current owner in Attio
- contact_owner: matches the account_owner of the associated company, UNLESS a
  contact-level override applies (see below)
- **Contact-level overrides (added 2026-06-22) — full rules in
  [../attio/round_robin_logic.txt](../attio/round_robin_logic.txt):**
    1. **Deal-owner override (highest):** if a company has an existing deal owned
       by an ACTIVE rep (any stage, incl. Disqualified), assign that company's
       account_owner AND ALL its contacts (FR + non-FR) to the deal owner.
       Fall back to the rules below if the deal owner is a former employee or an
       admin excluded from rotation.
    2. **Contact geography:** classify each contact by phone-number prefix
       (+33 = FR; other + = non-FR; no phone → Location). FR contacts → the
       Francophone pool; non-FR contacts → the Rest-of-World pool. contact_owner
       may diverge from account_owner.
    3. **Even split:** balance evenly within each pool, leaning the non-deal
       contacts toward the rep without a deal head start. Keep companies whole.
- Round robin pools (configure with your own team's rep identities):
    Francophone (FR, BE, CH, LU, MA, TN, DZ, SN, CI, CM, etc.):
      Slot 0: rep-a@example.com
      Slot 1: rep-b@example.com
    UK & Ireland (GB, UK, IE, JE):
      All -> rep-c@example.com (no rotation)
    Rest of World (all others / unknown country):
      Slot 0: rep-c@example.com
      Slot 1: rep-d@example.com
    Formula: MOD(row_index, pool_size) alternates within each territory pool
    (a single-rep territory has no rotation.)
- If country is unknown, default to Rest of World pool
- When a rep leaves, remove them from rotation and reassign any records they
  still own in the CRM to an active rep in the same run.

#### Column mapping: FINAL.csv to Attio API fields

**Company fields (extracted from FINAL.csv, deduplicated by domain):**

| FINAL.csv column               | Attio API field                  |
|--------------------------------|----------------------------------|
| Company Website                | domains [{"domain": "..."}]     |
| Company Name                   | name                             |
| Company Linkedin Flagship Url  | linkedin (fill if empty)         |
| Company HQ City                | primary_location.locality        |
| Company HQ Country             | primary_location.country_code    |
| Employee Range                 | employee_range                   |
| Company Industry               | industry                         |
| Company Description            | description                      |
| Year Founded                   | founded_year                     |
| (computed)                     | source_detail (fill if empty)    |
| (round robin)                  | account_owner (fill if empty)    |

**Contact fields:**

| FINAL.csv column               | Attio API field                  |
|--------------------------------|----------------------------------|
| Email                          | email_addresses                  |
| First Name                     | name[0].first_name               |
| Last Name                      | name[0].last_name                |
| Phone (Pronto)                 | phone_numbers                    |
| Title                          | job_title                        |
| Company Website                | company (name or domain)         |
| Linkedin Profile Url           | linkedin (fill if empty)         |
| Location                       | primary_location (parsed)        |
| (computed from title)          | persona                          |
| (default)                      | lead_status = "New"              |
| (default)                      | source = "Outbound"              |
| (computed)                     | source_detail (fill if empty)    |
| (matches account_owner)       | contact_owner (fill if empty)    |

#### Key API details
- Endpoint: PUT /v2/objects/{object}/records?matching_attribute={slug}
- Body: {"data": {"values": {...}}}
- phone_numbers format: [{"original_phone_number": "+33..."}] (NOT "phone_number")
- email_addresses: [{"email_address": "user@domain.com"}]
- domains: [{"domain": "example.com"}]
- primary_location: requires ALL subfields, use null for missing ones:
    ```json
    {"line_1": null, "line_2": null, "line_3": null, "line_4": null,
     "locality": "Paris", "region": "IDF", "postcode": null,
     "country_code": "FR", "latitude": null, "longitude": null}
    ```
- account_owner / contact_owner format:
    [{"referenced_actor_type": "workspace-member", "referenced_actor_id": "<UUID>"}]
- Workspace member UUIDs: look these up in your own CRM workspace and map each
  rep email to its workspace-member UUID (values are workspace-specific).

#### Run
```
cd {project_folder}
python3 ../attio/attio_import.py {project_name}_FINAL.csv
```

- The script expects Pronto-style column headers (see Step 6 Required FINAL.csv column headers table)
- Speed: ~1.5 min for 700 records (8 concurrent workers)
- At the end the script prints:
    - Import summary (created / updated / skipped / errors per object type)
    - Owner repartition table (new assignments only)
    - Any ambiguous matches to resolve manually in Attio

#### Enrichment maximization
After the core import, push any additional data from FINAL.csv to matching Attio fields. The goal is to fill as many Attio fields as possible by finding correspondences between FINAL.csv columns and Attio attribute definitions.

Steps:
1. List all Attio attribute definitions for companies and people (use list-attribute-definitions API)
2. For each extra column in FINAL.csv, find the best matching Attio field
3. For select/multiselect fields: check exact option titles before pushing (case sensitive). If no match, skip rather than error.
4. For text fields: push directly
5. Push company level data (pricing, CRM, billing) to company records (match by domain)
6. Push contact level data (comment) to people records (match by email)

Known field mappings for enrichment data:

| FINAL.csv column | Attio field (companies) | Type | Notes |
|---|---|---|---|
| pricing_model + pricing_plans | enriched_pricing_description | text | Combine as "Model: X \| Plans: Y" |
| pricing_page_url | pricing_page_link | text | |
| crm | crm | single select | Options: Salesforce, HubSpot, Pipedrive, Attio, Airtable |
| crm | enriched_crms | multiselect | Options: Salesforce, Pipedrive, Odoo, Sellsy (no HubSpot) |
| billing_tool | enriched_billing_and_payment_solutions | multiselect | Check option titles match exactly |
| comment (contact) | comment | text (people) | "Competitor of: ClientName" |

#### Error handling
- 429 rate limit: exponential backoff (2^attempt seconds, max 4 retries)
- Ambiguous match (multiple records for same key): skip row, log to error report
- Missing required field (no email for contact, no domain for company): skip row
- All skipped rows logged with reason for post import review

### Step 8: Lemlist Campaign Creation (API based)
- Script: process/step8_outreach_campaigns.py
- Usage:
    ```
    python3 step8_outreach_campaigns.py <project_name> \
        --input <FINAL.csv>
    ```
- Input: `{project_name}_FINAL.csv` (from Step 6)
- Tool: Lemlist API
- API key: env var LEMLIST_API_KEY
- Auth: Basic auth with empty username, API key as password
- Note: Lemlist Cloudflare blocks Python urllib. Use curl subprocess or requests with proper User-Agent.

#### Campaign structure
- 1 campaign per sales rep (same round robin assignment as Step 7)
- Naming convention: `{Project Descriptor} - {SalesRepFirstName}`
- Campaigns created in **PAUSED** state. Never auto start.
- After creation: assign sender identity + build sequence steps in Lemlist UI

#### Lead variables pushed to Lemlist
All available data is pushed as custom variables so AI columns can reference them:

| Category | Variables |
|---|---|
| Standard | firstName, lastName, email, phone, companyName, jobTitle, linkedinUrl, location |
| Company | companyDescription, companyWebsite, companyIndustry, companyLinkedinUrl, employeeRange |
| Pricing | pricingModel, pricingPageUrl, hasFreeTier, hasFreeTrial, pricingPlans |
| Stack | billingTool, paymentProvider, crm |
| Context | competitorOf (which Hyperline client they compete with) |

Push every non empty field from FINAL.csv. More data = better AI column personalization.

#### API details
- Create campaign: POST /api/campaigns `{"name": "..."}`
- Pause campaign: POST /api/campaigns/{id}/pause
- Add lead: POST /api/campaigns/{id}/leads/{email} `{variables}`
- List campaigns: GET /api/campaigns
- Rate limit: ~10 req/sec, add 0.1s sleep between lead additions

#### Pre launch checklist
1. Campaigns are paused. Do NOT start before sequence steps are configured.
2. Assign correct sender email identity per campaign in Lemlist UI
3. Build sequence steps (email, LinkedIn invite, LinkedIn message, follow ups)
4. Set up AI columns referencing the variables above
5. Review a sample of AI generated outputs before starting
6. Start campaigns one by one

---

### Iteration for Missing Companies (OPTIONAL)
If some companies have no contacts in FINAL.csv:
1. Start from `{project_name}_uncovered_companies.csv` (generated by Step 6)
2. Upload those companies as a new Pronto account list
3. Run the same Sales Nav title keyword search
4. Export leads, re-run Step 4 (include new exports alongside old ones via --contacts)
5. Re-run Steps 5 and 6

**Why companies go missing:**
- Pronto found no contacts matching the search filters for that company
- Common causes: company too small/no LinkedIn presence, wrong LinkedIn ID in the account list
- Expected: always some residual missing companies even after iteration (no public data)

---

## Output Format
- One row per contact (Attio compatible)
- Deduplication by LinkedIn URL first, then email
- Single output CSV per major step (no sub variants)

## API Notes
- Apollo base URL: https://api.apollo.io (header X-Api-Key, add Mozilla User-Agent to avoid 403)
- Pronto base URL: https://app.prontohq.com
- Pronto search: /api/v2/leads/search
- Pronto enrichment: /api/v2/contacts/single_enrich (async, needs webhook)
- TheirStack base URL: https://api.theirstack.com (Bearer token auth, 4 req/sec rate limit)
- TheirStack company search: POST /v1/companies/search (3 credits/company)
- Lemlist base URL: https://api.lemlist.com/api (Basic auth, empty username, API key as password)
- Lemlist campaign create: POST /api/campaigns
- Lemlist add lead: POST /api/campaigns/{id}/leads/{email}

## Credit Waste Prevention
Before ANY paid API call, always check the database first:
- TheirStack: check database/theirstack_companies_database.csv (match by domain)
    If domain found in DB: reuse company data, skip TheirStack call
- Apollo (Step 2): check database/apollo_companies_database.csv (match by domain)
    If domain found in DB: reuse linkedin_company_url + industry + naics_codes + keywords, skip Apollo call
- Pronto (Step 2b): check database/pronto_contacts_database.csv (match by company_linkedin_url)
    If company already has >=2 valid contacts in DB: reuse them, exclude company from Pronto import
- Pronto (Step 5): check database/pronto_contacts_database.csv (match by linkedin_url)
    If contact found in DB with email or phone: reuse it, skip Pronto enrichment call
- Rule: if the data already exists, reuse it, never re-enrich

## Apollo Companies Database
- Path: database/apollo_companies_database.csv
- This is a cumulative database. It grows with every project, never gets wiped
- Updated after every Step 2. Upsert new results (add if new, update if richer data available)
- Before Step 2: ALWAYS cross check input domains against this file first (match by domain)
- Columns stored:
    domain, company_name, linkedin_company_url, linkedin_uid,
    website_url, primary_phone, sanitized_phone,
    founded_year, industry, industries, naics_codes, sic_codes,
    estimated_num_employees, organization_revenue, organization_revenue_printed,
    street_address, city, state, country, postal_code,
    short_description, twitter_url, facebook_url, keywords,
    departmental_head_count, headcount_6m_growth, headcount_12m_growth,
    headcount_24m_growth, source_project, enriched_date

## Pronto Contacts Database
- Path: database/pronto_contacts_database.csv
- This is a cumulative database. It grows with every project, never gets wiped
- One row per contact (not per company)
- Updated after every Step 3 (search) and Step 5 (enrichment)
- Two lookup keys:
    1. company_linkedin_url: Step 2b: if company has >=2 valid contacts in DB, skip Pronto search
    2. linkedin_url: Step 5: if contact already has email or phone in DB, skip enrichment
- After Step 3 (search): append NEW contacts (those whose company was in the Pronto import)
- After Step 5 (enrichment): update enriched_date + email/phone for enriched contacts
- Columns stored:
    status, rejection_reasons, first_name, last_name, gender,
    email, email_status, phone, linkedin_url, linkedin_id_url,
    profile_image_url, location, title,
    years_in_position, months_in_position, years_in_company, months_in_company,
    company_name, company_cleaned_name, company_website, company_location,
    company_industry, company_linkedin_url, company_linkedin_id,
    company_employee_range, company_hq_city, company_hq_country,
    company_hq_postal, company_hq_region, company_description,
    source_project, searched_date, enriched_date

## TheirStack Companies Database
- Path: database/theirstack_companies_database.csv
- This is a cumulative database. It grows with every project, never gets wiped
- Used for lead sourcing (e.g. finding competitors' customers by technology stack)
- Before any TheirStack API call: check DB first by domain. If found, reuse and skip API call
- After search: append new results to the DB (never overwrite)
- API: POST /v1/companies/search (3 credits per company returned)
- Auth: Bearer token via THEIRSTACK_API_KEY env var
- Rate limit: 4 req/sec
- Columns stored:
    domain, company_name, company_linkedin_url, country_code,
    employee_count, industry, founded_year, funding_stage,
    total_funding_usd, technologies, description,
    source_query, source_project, enriched_date

## Key Decisions
- One row per contact format (not contact1/contact2 columns): Attio compatible
- COO included as relevant decision maker (Ops, Head-of+)
- "Manager" seniority excluded for Ops/Sales (too junior); Finance/Revenue keep all levels
- "Operations" function only used with seniority filter (too broad alone)
- Founders / CEO / President / GM dropped entirely (2026-06) — not a target persona
- Pure Sales dropped entirely (2026-06-16) — Head of Sales / VP Sales / Sales Director / CSO no longer a persona
- Sales Ops / Growth Ops / Business Ops kept at Head-of+ (Ops); Revenue Ops kept at all levels (Revenue);
  CCO / Chief Commercial Officer kept under Revenue (only PURE sales is dropped)
- Deduplication by LinkedIn URL first, then by email
