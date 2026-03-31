# Contact Title Rules: Search & Refinement

Single source of truth for Sales Nav search filters AND Step 4 refinement logic.

<!-- Define your own priority categories based on your ICP -->
**Priority:** Role Category A > Role Category B > Role Category C > Role Category D
Category D only relevant for companies with ≤[Your employee threshold] employees

**Matching (Step 4):** SUBSTRING / KEYWORD matching, not exact match.
- "CEO & Co-founder" matches both "CEO" and "Co-founder"
- "CFO / DAF" matches both "CFO" and "DAF"
- Short keywords (≤3 chars) must match as whole words (word boundary) to avoid false positives

---

## Sales Nav Copy Paste (Step 3)

Use ALL four filters together: function + seniority + title keywords + geography.

### Function Filters (select in Sales Nav)
[Your target functions, e.g. Business Development, Sales, Finance, Operations, etc.]

### Seniority Level Filter (select in Sales Nav)
CXO, Director, Vice President, Strategic, Owner / Partner

### Geography Filter (select in Sales Nav)
[Your target geographies, e.g. France, United States, United Kingdom, etc.]

### Job Title Inclusions (add in "Current Job Title")
[Your target title keywords here, covering all priority categories]

### Job Title Exclusions
No exclusions in Sales Nav. Step 4 handles all exclusion filtering.

### Notes
- All four filters combined: function + seniority + title keywords + geography
- The 4 filters together give a tight result set, no exclusions needed in search
- Optionally scope to an Account List (pre filters to target companies)
- Step 4 handles final refinement (exclusions, company check, max 2/company, Category D employee threshold)

---

## Inclusion List: Step 4 Refinement (by priority category)

<!-- Define your own priority categories based on your ICP -->
<!-- Each category should list the job titles you want to target, ordered by priority -->
<!-- The script picks the top 2 contacts per company using this priority order -->

### 1. Role Category A (highest priority)
[List your priority 1 role titles here]
Example structure:
- Head of [Function]
- VP [Function]
- Director of [Function]
- Chief [Function] Officer
- Senior Director of [Function]

### 2. Role Category B
[List your priority 2 role titles here]
Example structure:
- CFO / Chief Financial Officer
- VP Finance
- Head of Finance
- Finance Director
- Financial Controller

### 3. Role Category C
[List your priority 3 role titles here]
Example structure:
- COO / Chief Operating Officer
- VP Operations
- Head of Operations
- Director of Operations
- General Manager

### 4. Role Category D (only if ≤[Your employee threshold] employees)
[List your priority 4 role titles here]
Example structure:
- Founder / Co-Founder
- CEO / Chief Executive Officer
- President
- Managing Partner

---

## Exclusion List: Step 4 Refinement

If any exclusion keyword appears in the title (substring), exclude. Exclusions take priority over inclusions.

<!-- Define your own exclusion categories below -->
<!-- Group them by reason for exclusion so they're easy to maintain -->

### Too Junior
[List titles that are too junior for your ICP, e.g.]
- [Function] Manager
- [Function] Associate
- [Function] Specialist
- [Function] Analyst
- Assistant
- Office Manager

### Wrong C-Suite / Wrong Department
[List C-suite or department heads outside your ICP, e.g.]
- CTO / Chief Technology Officer
- CPO / Chief Product Officer
- CMO / Chief Marketing Officer
- Chief of Staff

### Wrong Function Type
[List roles in adjacent but irrelevant functions, e.g.]
- IT Operations
- Technical Operations
- DevOps
- Back Office

### Board / Investors / Advisors
- Board Member
- Board Director
- Investor
- Business Angel
- Advisor
- Mentor

### Investment / Fund Keywords
- Venture Capital
- Private Equity
- Fund Manager
- General Partner
- Portfolio Manager

### Consulting / Fractional Keywords
- Fractional
- Interim
- Consultant
- Freelance

### Project / Program Directors
- Project Director
- Program Director
- Head of Project Management

---

## Priority Categories (for max 2 per company selection in Step 4)

<!-- Define your own priority categories based on your ICP -->
1. **Category A** : [Your priority 1 roles, e.g. Revenue Operations, CRO, Head of Revenue]
2. **Category B** : [Your priority 2 roles, e.g. CFO, VP Finance, Head of Finance]
3. **Category C** : [Your priority 3 roles, e.g. COO, VP Operations, General Manager]
4. **Category D** : [Your priority 4 roles, e.g. CEO, Founder, President] (only if ≤[Your employee threshold] emp)
