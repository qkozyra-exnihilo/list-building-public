# B2B List Building Process

Last updated: 2026-03-27

## Goal
Build a high quality B2B contact list from any company list, enriched with relevant contacts, emails, and phone numbers.

## ICP: Company Filter
<!-- Define your own target vertical below -->
- Target: [Your target vertical / company type here]
- All rules in one file: see [company_search_filter_rules.md](company_search_filter_rules.md)
  (industry list, NAICS prefixes, keyword include/exclude lists, filter logic)
- Applied after Apollo enrichment (Step 2) using 3 layers:
    1. Industry match (Apollo `industry` field)
    2. NAICS match (prefix check)
    3. Keyword confirmation (AND operator, Apollo `keywords` field)

## Input
- A list of companies with: company name + domain (LinkedIn URL optional)
- Sectors to exclude can be specified upfront

## Target Contacts (Priority Order)
<!-- Define your own priority categories based on your ICP -->
1. Role Category A (e.g. [Your priority 1 role titles here])
2. Role Category B (e.g. [Your priority 2 role titles here])
3. Role Category C (e.g. [Your priority 3 role titles here])
4. Role Category D (e.g. [Your priority 4 role titles here])
   Category D only relevant for companies with ≤[Your employee threshold] employees

## Title Filters
- All rules in one file: see [contact_title_rules.md](contact_title_rules.md)
  (Sales Nav copy paste filters + Step 4 refinement inclusion/exclusion lists)
- Exclusions: [Your exclusion categories here, e.g. board, investor, advisor, wrong department, too junior roles]

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

### Step 2: Apollo Enrichment (Company LinkedIn URLs)
- Script: process/step2_apollo_enrichment.py
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
- **ICP filter (industry/NAICS + keyword confirmation):** after enrichment,
  apply the filter defined in the ICP section above.
  1. Check industry and NAICS against target lists
  2. If match, confirm with keywords (include/exclude/consulting rules)
  3. If no industry AND no NAICS, keep for manual review
  4. If no keywords in DB but industry/NAICS matches, keep (can't verify)
  Report: N kept (confirmed by keywords), N kept (no keywords), N kept (no data), N dropped.
- Output file: `{project_name}_step2_companies_with_linkedin.csv`
    Columns: all step1c columns + linkedin_company_url + industry + naics_code + naics_label
    Only includes companies passing ICP filter (industry/NAICS + keyword confirmation)

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

### Step 3: Pronto Contact Search (MANUAL)
- Tool: Pronto UI + LinkedIn Sales Navigator
- Approach: all 4 filters combined: function + seniority + title keywords + geography
- Can be scoped to an account list OR run as a broad search (Step 4 filters later)

#### What you do (manual, 2 steps)
1. **Run a Sales Nav search** in Pronto UI with:
   - **Function:** [Your target functions]
   - **Seniority level:** CXO, Director, Vice President, Strategic, Owner / Partner
   - **Geography:** [Your target geographies]
   - **Job Title inclusion keywords** (covering all target roles):
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

### Step 4: Contact Refinement (keep 2 per company)
- Script: process/step4_contact_refinement.py
- Input: one or more Pronto export CSVs + optional DB contacts CSV
- Usage:
    ```
    python3 step4_contact_refinement.py <project_name> \
        --companies <step2_companies.csv> \
        --contacts <pronto_export1.csv> [<pronto_export2.csv> ...] \
        [--db-contacts <step2b_db_contacts.csv>]
    ```
- Cross reference against company list (match by domain or Company LinkedIn URL)
- Apply inclusion/exclusion rules ([contact_title_rules.md](contact_title_rules.md))
- Remove: [Your exclusion categories, e.g. board members, investors, wrong department, too junior]
- Remove Category D roles from companies with >[Your employee threshold] employees
- Keep max 2 contacts per company, prioritized in this order:
    1. Role Category A (highest priority)
    2. Role Category B
    3. Role Category C
    4. Role Category D (only if ≤[Your employee threshold] employees)
- Output file: `{project_name}_step4_contacts_filtered.csv`
    One row per contact, max 2 per company, relevant titles only

### Step 5: Pronto Enrichment (Email + Phone)
- Script: process/step5_pronto_enrichment.py
- Tool: Pronto API (/api/v2/contacts/single_enrich)
- Usage:
    ```
    python3 step5_pronto_enrichment.py <project_name> \
        --input <step4_contacts_filtered.csv> \
        [--db <pronto_contacts_database.csv>]
    ```
- Async: requires webhook server (Python HTTP server + localtunnel)
- Output: email + phone added to each contact row
- Pronto credit pricing (credits only consumed on successful finds):
    - 1 email found = 3 credits
    - 1 phone number found = 30 credits
    - No charge if nothing found
- DB cache: auto checks database/pronto_contacts_database.csv by linkedin_url
    - If contact found with email OR phone, reuse, skip enrichment call
    - Only enriches contacts missing BOTH email AND phone
    - Appends new contacts to DB after enrichment
- Output file: `{project_name}_step5_contacts_enriched.csv`
    All contacts + email + phone

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
- Output files:
    - `{project_name}_FINAL.csv`: final contacts deliverable, ready for outreach platform import
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

### Step 7: Outreach Campaign Creation (API based)
- Script: process/step8_outreach_campaigns.py
- Input: `{project_name}_FINAL.csv` (from Step 6)
- Tool: your outreach/sequencing platform API
- API key: env var for your sequencing tool API key
- Auth: Basic auth with empty username, API key as password
- Note: some platforms block Python urllib. Use curl subprocess or requests with proper User-Agent.

#### Campaign structure
- 1 campaign per sales rep (assigned by territory based round robin)
- Naming convention: `{Project Descriptor} - {SalesRepFirstName}`
- Campaigns created in **PAUSED** state. Never auto start.
- After creation: assign sender identity + build sequence steps in the sequencing tool UI

#### Lead variables pushed to the outreach platform
All available data is pushed as custom variables so AI columns can reference them:

| Category | Variables |
|---|---|
| Standard | firstName, lastName, email, phone, companyName, jobTitle, linkedinUrl, location |
| Company | companyDescription, companyWebsite, companyIndustry, companyLinkedinUrl, employeeRange |
| Pricing | pricingModel, pricingPageUrl, hasFreeTier, hasFreeTrial, pricingPlans |
| Stack | billingTool, paymentProvider, crm |
| Context | competitorOf (which YourCompany client they compete with) |

Push every non empty field from FINAL.csv. More data = better AI column personalization.

#### API details
- Create campaign: POST /api/campaigns `{"name": "..."}`
- Pause campaign: POST /api/campaigns/{id}/pause
- Add lead: POST /api/campaigns/{id}/leads/{email} `{variables}`
- List campaigns: GET /api/campaigns
- Rate limit: ~10 req/sec, add 0.1s sleep between lead additions

#### Pre launch checklist
1. Campaigns are paused. Do NOT start before sequence steps are configured.
2. Assign correct sender email identity per campaign in the sequencing tool UI
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
- One row per contact
- Deduplication by LinkedIn URL first, then email
- Single output CSV per major step (no sub variants)

## API Notes
- Apollo base URL: https://api.apollo.io (header X-Api-Key, add Mozilla User-Agent to avoid 403)
- Pronto base URL: https://app.prontohq.com
- Pronto search: /api/v2/leads/search
- Pronto enrichment: /api/v2/contacts/single_enrich (async, needs webhook)
- TheirStack base URL: https://api.theirstack.com (Bearer token auth, 4 req/sec rate limit)
- TheirStack company search: POST /v1/companies/search (3 credits/company)
- Outreach platform API base URL (Basic auth, empty username, API key as password)
- Campaign create: POST /api/campaigns
- Add lead to campaign: POST /api/campaigns/{id}/leads/{email}

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
- One row per contact format (not contact1/contact2 columns)
- Define your own priority role categories and exclusion rules in contact_title_rules.md
- "Manager" seniority excluded (too junior) -- adjust threshold to your needs
- Role Category D excluded from companies with >[your employee threshold] employees
- Deduplication by LinkedIn URL first, then by email
