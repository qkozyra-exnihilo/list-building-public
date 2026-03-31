# B2B List Building Pipeline

A structured pipeline that takes a company list and outputs enriched contacts ready for outreach campaigns. Built for B2B sales teams targeting any vertical.

## The Flow

```
Company list (CSV)
  |
  v
Step 1 .... Domain cleaning + deduplication
  |
  v
Step 1c ... Domain & LinkedIn verification (AI assisted)
  |
  v
Step 2 .... Apollo enrichment (company LinkedIn URLs + ICP filtering)
  |
  v
Step 2b ... Database check (skip companies already enriched)
  |
  v
Step 3 .... Pronto contact search (via LinkedIn Sales Navigator)
  |
  v
Step 4 .... Title filtering (max 2 contacts per company)
  |
  v
Step 5 .... Email + phone enrichment (Pronto API)
  |
  v
Step 6 .... Final output (clean CSV)
  |
  v
Step 7 .... Outreach campaign creation (API based)
```

## Prerequisites

**Python 3.9+**

**Node.js / npx** (required for localtunnel, used by the Pronto webhook enrichment step)

**localtunnel** (npm package):
```
npm install -g localtunnel
```

**LinkedIn Sales Navigator account** (required for Pronto contact search)

**API keys** (stored as environment variables in `~/.zshrc`):

| Variable | Purpose |
|---|---|
| `APOLLO_API_KEY` | Company enrichment (LinkedIn URLs, industry data) |
| `PRONTO_API_KEY` | Contact search + email/phone enrichment |
| `OUTREACH_API_KEY` | Outreach platform (e.g. Lemlist, Apollo Sequences, or similar) |
| `THEIRSTACK_API_KEY` | Optional. Technology stack sourcing for lead generation |

## Folder Structure

```
list-building/
  process/            Process documentation + pipeline scripts
    process.md          Full step by step walkthrough
    step4_*.py          Contact refinement (title filtering, max 2 per company)
    step5_*.py          Email + phone enrichment via Pronto API
    step6_*.py          Final output generation
    *_rules.md          ICP filters, title rules, company enrichment rules

  database/           Cumulative enrichment databases (not in repo)
    apollo_companies_database.csv     Company data cache
    pronto_contacts_database.csv      Contact data cache
    theirstack_companies_database.csv Technology stack cache

  {project_name}/     One folder per list building project
                      Contains step by step CSVs from input to FINAL.csv
```

The `database/` folder is created automatically on first run. It stores cumulative enrichment data across all projects to avoid paying for duplicate API calls.

## Scripts

| Script | Description |
|---|---|
| `process/step1_company_list.py` | Standardize seed CSV to common format |
| `process/step1b_company_cleaning.py` | Deduplicate and fix domains |
| `process/step1c_domain_verification.py` | AI assisted domain and LinkedIn lookup |
| `process/step2_apollo_enrichment.py` | Apollo enrichment with DB caching |
| `process/step2b_db_contact_check.py` | Check existing contacts in DB |
| `process/step4_contact_refinement.py` | Title filtering, max 2 per company |
| `process/step5_pronto_enrichment.py` | Email and phone enrichment |
| `process/step6_final_output.py` | Generate final deliverable |
| `process/step8_outreach_campaigns.py` | Create outreach campaigns per rep |

## Quick Start

1. Set up your API keys as environment variables
2. Place your company list CSV in a new project folder
3. Follow the detailed walkthrough in [`process/process.md`](process/process.md)

Each step produces a clearly named CSV (`{project}_step1_companies.csv`, `{project}_step2_companies_with_linkedin.csv`, etc.) so you can inspect intermediate results and resume from any point.

## Key Design Decisions

**Max 2 contacts per company, with priority ranking:**
<!-- Define your own priority categories based on your ICP -->
1. Priority 1 Role (e.g. Head / VP / Director level in your target function)
2. Priority 2 Role (e.g. Finance leadership)
3. Priority 3 Role (e.g. Operations leadership)
4. Priority 4 Role (e.g. Founders/CEO, only for companies with [your employee threshold] or fewer employees)

**Database caching to prevent duplicate enrichments.** Every paid API call first checks the local database. If a company or contact has already been enriched in a previous project, the cached data is reused and no credits are spent.

**ICP filtering with three layers.** After Apollo enrichment, companies are filtered through industry match, NAICS code match, and keyword confirmation to ensure only relevant targets proceed through the pipeline. Define your own target industries, NAICS codes, and keyword lists in `process/company_search_filter_rules.md`.

**Title exclusions to maintain contact quality.** Define your exclusion categories (e.g. board members, investors, advisors, wrong department, too junior roles) in `process/contact_title_rules.md`. Founders can be excluded from companies above a configurable employee threshold.
