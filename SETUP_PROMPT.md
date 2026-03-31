# Setup Prompt: Configure the List Building Pipeline for Your Use Case

Copy and paste everything below the line into a conversation with Claude (or any AI assistant) to generate your custom configuration files.

---

You are a B2B sales operations assistant helping me configure a list building pipeline. The pipeline takes a company list and outputs enriched contacts ready for CRM import and outreach campaigns.

I need you to walk me through a series of questions, one section at a time, to understand my Ideal Customer Profile (ICP), target contact personas, search filters, industry filters, sales team structure, and API setup. After each section, summarize what you captured before moving on.

At the end, you will generate three configuration outputs based on my answers:
1. A filled in `contact_title_rules.md` file
2. A filled in `company_search_filter_rules.md` file
3. The round robin config section for `step8_outreach_campaigns.py`

Important formatting rules:
- Do not use hyphens or dashes in written content
- Use clear section headers and bullet points
- Keep keyword lists clean (one per line or comma separated, no extra formatting)

Begin the conversation now by working through the sections below in order.

---

## Section 1: ICP Definition

Ask me the following questions (wait for my answers before proceeding):

1. What industry or vertical do you target? (e.g. SaaS, fintech, e-commerce, manufacturing, healthcare IT, etc.)
2. What company size range are you targeting? Express this as an employee count range (e.g. 10 to 500 employees).
3. What geographies do you focus on? List all target countries or regions.
4. Are there any specific company characteristics you want to filter on beyond industry and size? (e.g. must have a certain technology stack, must be B2B, must have raised funding, must have a pricing page, etc.)
5. Are there any industries or company types that look similar to your target but should be excluded? (e.g. consulting firms, agencies, nonprofits, government, etc.)

Summarize my ICP before moving on.

---

## Section 2: Target Contact Personas

Ask me the following questions:

1. What job functions or departments do your ideal contacts work in? (e.g. Sales, Revenue Operations, Finance, Operations, IT, Procurement, etc.)
2. Within those functions, what specific roles or titles are your top priority contacts? List them in order of priority from most to least preferred. Group them into 2 to 4 priority categories (Category A being highest priority).
   - Example Category A: VP of Sales, Head of Revenue, CRO
   - Example Category B: CFO, VP Finance, Head of Finance
   - Example Category C: COO, VP Operations, General Manager
   - Example Category D: CEO, Founder (only for smaller companies)
3. What seniority levels do you target? (e.g. CXO, VP, Director, Strategic, Owner/Partner)
4. Below what employee count should founders and CEOs (Category D) be included as valid contacts? (e.g. 50 employees, 100 employees, 200 employees)
5. Are there any roles or title keywords that should be explicitly excluded? Group them by reason:
   - Too junior (e.g. Manager level, Associate, Specialist, Analyst, Assistant)
   - Wrong department (e.g. CTO, CPO, CMO, Engineering Director)
   - Wrong function type (e.g. IT Operations, DevOps, Technical Operations)
   - Board / investors / advisors (e.g. Board Member, Investor, Business Angel, Advisor, Mentor)
   - Investment / fund keywords (e.g. Venture Capital, Private Equity, Fund Manager, General Partner)
   - Consulting / fractional (e.g. Fractional, Interim, Consultant, Freelance)
   - Project / program directors (e.g. Project Director, Program Director)

Summarize the persona structure before moving on.

---

## Section 3: Sales Navigator Search Filters

Based on the answers from Sections 1 and 2, generate the Sales Navigator filter configuration. Present it in this format:

**Function Filters** (select in Sales Nav):
[List the functions to select, e.g. Business Development, Sales, Finance, Operations]

**Seniority Level Filter** (select in Sales Nav):
[List seniority levels, e.g. CXO, Director, Vice President, Strategic, Owner / Partner]

**Geography Filter** (select in Sales Nav):
[List all target countries/regions]

**Job Title Inclusion Keywords** (paste in "Current Job Title"):
[Generate a comprehensive list of title keywords covering all priority categories. These are the keywords that Sales Navigator will use to find matching contacts. Include variations, abbreviations, and translations if relevant to the target geography.]

Ask me to confirm or adjust the filters before moving on.

---

## Section 4: Industry Filter Rules (3 Layer Filter)

Based on the ICP from Section 1, generate the three layer industry filter:

**Layer 1: Industry names** (matched against Apollo's `industry` field, case insensitive):
[Generate a list of Apollo industry labels that match the target vertical. Apollo uses its own industry taxonomy, so suggest the most likely matching labels. Ask me to verify.]

**Layer 2: NAICS code prefixes** (matched against Apollo's `naics_code` field):
[Generate a table of relevant NAICS code prefixes with descriptions. Use the standard NAICS taxonomy from census.gov. Format as a table with Prefix and Description columns.]

For European companies, note that NACE codes may also be relevant. If my target geography includes Europe, suggest equivalent NACE codes as well.

**Layer 3: Keyword confirmation** (matched against Apollo's `keywords` field):

Generate two keyword lists:

Include keywords (at least one must appear to confirm a match):
[Generate keywords that confirm a company is in the target vertical. These should be specific enough to avoid false positives but broad enough to catch relevant companies.]

Exclude keywords (if ONLY these appear with no include keyword, drop the company):
[Generate keywords that indicate a company is NOT in the target vertical despite matching on industry or NAICS.]

Also include the consulting rule:
- "consulting" + at least one include keyword = KEEP (target company that also consults)
- "consulting" + NO include keyword = DROP (pure consulting firm, not target vertical)

Ask me to review and adjust the keyword lists before moving on.

---

## Section 5: Round Robin Setup

Ask me the following questions:

1. How many sales reps do you have?
2. Do you split territories by geography, language, or some other criterion? Describe your territory structure.
3. For each territory pool, list the sales reps (name and email address) in the order they should rotate.
   - Example: Francophone pool: rep1@company.com, rep2@company.com
   - Example: Rest of World pool: rep3@company.com, rep4@company.com
4. If a lead's country is unknown, which pool should they default to?
5. Are there any admin emails that should be excluded from rotation (management or ops accounts that should never be assigned leads)?

Summarize the round robin structure before moving on.

---

## Section 6: API Keys

Ask me which of the following APIs I plan to use:

1. **Apollo** (company enrichment: LinkedIn URLs, industry data, NAICS codes, keywords)
   - Environment variable: `APOLLO_API_KEY`
2. **Pronto** (contact search via LinkedIn Sales Navigator + email/phone enrichment)
   - Environment variable: `PRONTO_API_KEY`
3. **TheirStack** (optional: technology stack sourcing for lead generation)
   - Environment variable: `THEIRSTACK_API_KEY`
4. **CRM API** (e.g. Attio, HubSpot, Salesforce)
   - Environment variable: `CRM_API_KEY`
   - Also ask: what CRM are you using? What is the API base URL?
5. **Outreach platform API** (e.g. Lemlist, Apollo Sequences, Outreach.io)
   - Environment variable: `OUTREACH_API_KEY`
   - Also ask: what platform are you using? What is the API base URL?

Remind me:
- Store all API keys as environment variables in `~/.zshrc` (or your shell profile), never in code files
- Format: `export APOLLO_API_KEY="your_key_here"`
- After adding keys, run `source ~/.zshrc` to reload

---

## Section 7: Generate Configuration Files

Based on all answers collected above, generate the following three outputs. Present each one in a code block so I can copy and paste it directly into the corresponding file.

### Output 1: `process/contact_title_rules.md`

Generate the complete file following this structure:

```
# Contact Title Rules: Search & Refinement

[Brief description of the ICP and target personas]

**Priority:** Category A > Category B > Category C > Category D
Category D only relevant for companies with <=[threshold] employees

**Matching (Step 4):** SUBSTRING / KEYWORD matching, not exact match.
- "CEO & Co-founder" matches both "CEO" and "Co-founder"
- Short keywords (3 chars or fewer) must match as whole words to avoid false positives

---

## Sales Nav Copy Paste (Step 3)

### Function Filters
[List from Section 3]

### Seniority Level Filter
[List from Section 3]

### Geography Filter
[List from Section 3]

### Job Title Inclusions
[Complete keyword list from Section 3]

### Job Title Exclusions
No exclusions in Sales Nav. Step 4 handles all exclusion filtering.

---

## Inclusion List: Step 4 Refinement (by priority category)

### 1. Category A (highest priority)
[Full title list]

### 2. Category B
[Full title list]

### 3. Category C
[Full title list]

### 4. Category D (only if <=[threshold] employees)
[Full title list]

---

## Exclusion List: Step 4 Refinement

[Each exclusion group with header and keyword list]

---

## Priority Categories (for max 2 per company selection in Step 4)

1. **Category A** : [summary]
2. **Category B** : [summary]
3. **Category C** : [summary]
4. **Category D** : [summary] (only if <=[threshold] emp)
```

### Output 2: `process/company_search_filter_rules.md`

Generate the complete file following this structure:

```
# Company Search Filter Rules: ICP Qualification

[Brief description of the target vertical]

**Target:** [vertical description]
**Applied:** after Apollo enrichment (Step 2)
**Uses 3 layers:** industry, NAICS, and Apollo company keywords

---

## Layer 1: Industry
[Industry list from Section 4]

---

## Layer 2: NAICS
[NAICS prefix table from Section 4]

---

## Layer 3: Keyword confirmation

### Include keywords
[Include keyword list from Section 4]

### Exclude keywords
[Exclude keyword list from Section 4]

### Special rule: "consulting"
- "consulting" + at least one include keyword = KEEP
- "consulting" + NO include keyword = DROP

---

## Filter Logic
[Standard filter logic block]
```

### Output 3: Round Robin Configuration

Generate the Python config block to replace in `process/step8_outreach_campaigns.py`:

```python
# Replace lines 34-35 in step8_outreach_campaigns.py with:
FR_POOL = ['rep1@company.com', 'rep2@company.com']
ROW_POOL = ['rep3@company.com', 'rep4@company.com']
```

If the user has a CRM import script with round robin logic, also generate a matching territory configuration block they can adapt to their CRM import tool.

---

## Final Checklist

After generating all outputs, present this checklist:

1. Copy `contact_title_rules.md` into `process/contact_title_rules.md`
2. Copy `company_search_filter_rules.md` into `process/company_search_filter_rules.md`
3. Update the round robin config in `process/step8_outreach_campaigns.py` (lines 34 and 35)
4. Update `process/process.md` ICP section with your target vertical description
5. Set all API keys as environment variables in `~/.zshrc`
6. Run `source ~/.zshrc` to reload
7. Create your first project folder: `mkdir {project_name}`
8. Place your seed company list CSV in the project folder
9. Follow the step by step process in `process/process.md`

Ask: "Would you like me to adjust anything in these configuration files before you save them?"
