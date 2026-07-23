# Contact Title Rules: Search & Refinement

Single source of truth for Sales Nav search filters AND Step 4 refinement logic.

**Priority (reprioritized 2026-06; Sales dropped 2026-06-16):** Finance > Revenue > Ops

1. **Finance** — CFO, VP/Head/Director Finance, FP&A, DAF, finance-operations (Controllers excluded).
2. **Revenue (the revenue team)** — revenue *leadership* (CRO, Chief Revenue Officer,
   Head/VP Revenue, Revenue Director, CCO / Chief Commercial Officer) **and** Revenue
   Operations / RevOps.
3. **Ops** — general operations / COO **plus** Sales Ops, Growth Ops and Business Ops.

> **Sales is NO LONGER a target persona (dropped 2026-06-16).** Pure-sales leadership
> (Head of Sales, VP Sales, Sales Director, Chief Sales Officer / CSO, Head of Commercial,
> Directeur Commercial, Directeur des Ventes) and all sales ICs (AE, AM, KAM, SDR, BDR,
> Business Developer, Sales Manager, Partnerships) are dropped at any level. NB the two
> things that look like sales but are KEPT: **Sales Operations** at Head-of+ (→ Ops) and
> **CCO / Chief Commercial Officer** (→ Revenue).

**Level rules:**
- **Finance, Revenue** — keep ALL levels, including junior (Finance Lead, Finance
  Operations, Finance Manager, FP&A Analyst; RevOps Analyst/Manager, Revenue Operations
  Specialist). Exclude only purely clerical bookkeeping (a plain Accountant/Comptable/AP
  clerk with no lead/analyst scope).
- **Ops** — keep ONLY "Head of" level and above (Head/VP/Director/Chief): general
  Operations/COO, Sales Operations, Growth Operations and Business Operations at Head-of+;
  below-Head ops (Operations Manager, Sales Operations Manager, Ops Analyst/Specialist/
  Coordinator) are dropped. Still EXCLUDED entirely (wrong ops type): Marketing Ops,
  IT/Technical Ops, DevOps, People/HR Ops, Customer Ops, Product Ops, AI Ops. General
  Manager is NOT kept.
- **Overlap rule:** Revenue Operations sits in both Revenue (#2) and Ops (#3). Priority
  wins → it is classified as **Revenue (#2) and kept at ALL levels**. Sales Ops / Growth
  Ops remain Ops (#3), Head-of+ only.
- **Founders / CEO / President / GM — NOT a persona.** Founder, Co-Founder, CEO,
  President, General Manager, and the French equivalents (PDG, DG, Directeur Général,
  Gérant, Dirigeant, DGA) are dropped at ANY company size. A combined title still
  resolves by its qualifying function (e.g. "CEO & CFO" → Finance).

**Keep-all + phone cap:** Step 4 now keeps ALL relevant contacts (no per-company cap).
The "max 2 per company" now lives in Step 5 and applies to PHONE enrichment only
(phone ≈ 10× email credits). Email is enriched for everyone; phone for the top 2 per
company, ranked by persona priority + the LLM fit_score.

## Hybrid classification (deterministic + LLM)

Step 4 is a two-stage hybrid (see `step4_contact_refinement.py` + `persona_judge.py`):

- **Stage A — deterministic noise removal (`HARD_DROP`):** drops guaranteed
  non-personas with no model call (investors, board, advisors, VC/fund, students,
  interns, freelance/fractional/consultant, wrong C-suite: CTO/CPO/CMO/CIO/CDO/CoS).
- **Stage B — LLM persona judge (PRIMARY):** every contact that survives Stage A is
  judged by Claude against the rubric above → `{keep, category, seniority_tier,
  fit_score, reason}`. The LLM is never gated behind keyword *inclusion*, so title
  variants the dictionary never anticipated (e.g. "Finance Lead", "Finance Operations",
  "Manager, Finance EMEA") are always assessed on their merits — fixing the silent
  "falls through the gap" misses that pure keyword matching produced.
- **Reproducible + auditable:** temperature 0, cached verdicts in
  `database/persona_judgments.csv` (keyed by title|headline|employee-bucket), every
  keep/drop carries a `reason`. Run `--no-llm` to force the deterministic fallback.

The keyword inclusion/exclusion lists below are now the **deterministic fallback**
(used with `--no-llm` or if the API errors per contact) and the source for the Sales
Nav search filters. They are no longer the primary keep/drop authority.

**Matching (deterministic fallback):** SUBSTRING / KEYWORD matching, not exact match.
- "CEO & Co-founder" matches both "CEO" and "Co-founder"
- "CFO / DAF" matches both "CFO" and "DAF"
- Short keywords (≤3 chars: DG, DAF, PDG, VC, COO, CEO, CFO, CRO, DGA, CSO) must match as whole words (word boundary) to avoid false positives

---

## Sales Nav Copy Paste (Step 3)

Use ALL four filters together: function + seniority + title keywords + geography.

### Function Filters (select in Sales Nav)
Business Development, Sales, Finance, Accounting, Operations

### Seniority Level Filter (select in Sales Nav)
CXO, Director, Vice President, Strategic

### Geography Filter (select in Sales Nav)
France, United States, United Kingdom, Belgium, Switzerland, Luxembourg, Jersey

### Job Title Inclusions (add in "Current Job Title")

CFO, COO, CRO,
Head of Finance, VP Finance, Finance Lead, Finance Manager,
Head of Operations, VP Operations,
Head of Revenue, RevOps, Revenue Operations, CCO,
Head of Sales Operations, Head of Growth Operations, Head of Business Operations,
DAF

> Pure-sales titles (Head of Sales, VP Sales, Sales Director) were removed from the
> search inclusions on 2026-06-16 — Sales is no longer a target persona, so pulling them
> only wastes search/Step-4 effort. Sales **Operations** is kept (it maps to Ops).

### Job Title Exclusions
No exclusions in Sales Nav. Step 4 handles all exclusion filtering.

### Notes
- All four filters combined: function + seniority + title keywords + geography
- The 4 filters together give a tight result set, no exclusions needed in search
- Optionally scope to an Account List (pre filters to target companies)
- Step 4 handles final refinement (exclusions, company check, founders dropped); phone cap of 2/company lives in Step 5

---

## Inclusion List: Step 4 Refinement (by priority category)

### 1. Finance (highest priority)
- CFO
- Chief Financial Officer
- Chief Accounting Officer
- VP Finance
- VP of Finance
- Vice President Finance
- Head of Finance
- Finance Director
- Financial Director
- Director of Finance
- Group CFO
- Acting CFO
- SEVP Finance
- VP Financial Operations
- Head of Financial Operations
- Director of Financial Planning
- VP FP&A
- Head of FP&A
- VP Accounting
- VP of Accounting
- Head of Accounting
- Director of Accounting
- Finance Manager
- Directeur Financier
- Directrice Financière
- Directeur Administratif et Financier
- Directrice Administrative et Financière
- DAF
- Responsable Financier
- Responsable Financière

> **Controllers dropped (2026-06-09).** Controller, Financial Controller, Group
> Controller and Contrôleur/Contrôleuse de Gestion are NOT kept (too operational /
> backward-looking). They are dropped by matching no inclusion — NOT added to the
> exclusion list, so a combined title like "CFO & Group Controller" is still kept
> by the CFO part.

### 2. Revenue — leadership + RevOps (ALL levels)
Revenue leadership:
- CRO
- Chief Revenue Officer
- VP Revenue
- VP of Revenue
- Head of Revenue
- Revenue Director
- Director of Revenue
- CCO
- Chief Commercial Officer

Revenue Operations / RevOps (kept at ALL levels — incl. Manager / Analyst / Specialist / Associate):
- Revenue Operations
- RevOps
- Head of Revenue Operations
- VP / VP of Revenue Operations
- Director of / Director Revenue Operations
- Director of RevOps, Head of RevOps, VP RevOps
- Global Head of Revenue Operations
- Senior Director of Revenue Operations
- Revenue Operations Manager / Analyst / Specialist / Associate / Lead

### 3. Ops — Head-of+ only (general ops + Sales Ops + Growth Ops + Business Ops)
General operations / COO:
- COO
- Chief Operating Officer
- Chief Operations Officer
- VP Operations
- Vice President Operations
- Head of Operations
- Director of Operations
- Operations Director
- Founding Chief Operating Officer
- Directeur des Opérations / Directrice des Opérations
- Directeur Opérationnel / Directrice Opérationnelle
- Directeur d'Exploitation / Directrice d'Exploitation

Sales Operations (Head-of+ only):
- Head of Sales Operations
- VP / VP of Sales Operations
- Director of Sales Operations
- Sales Operations Director

Growth Operations (Head-of+ only):
- Head of Growth Operations
- VP Growth Operations
- Director of Growth Operations
- Growth Operations Director

Business Operations / general ops (Head-of+ only):
- Head of Business Operations
- VP Business Operations
- Director of Business Operations
- Business Operations Director

> Below-Head ops (Operations Manager, Sales Operations Manager, Ops Analyst /
> Specialist / Coordinator) are NOT kept — they match no Head-of+ inclusion and drop.
> Revenue Operations is the exception: it resolves to Revenue (#2) at ALL levels.

> **Founders / CEO / President / GM removed 2026-06.** This category no longer
> exists. Founder, Co-Founder, CEO, President, General Manager, Entrepreneur and
> the French equivalents (Directeur Général, DG, PDG, Président, Gérant, Associé
> Gérant, Fondateur, Cofondateur, Dirigeant, DGA, Directeur Général Adjoint) are
> dropped at every company size. A combined title still resolves by its qualifying
> function (e.g. "CEO & CFO" → Finance). Note: founder/CEO/GM titles are NOT added
> to the exclusion list — that would outrank inclusions and kill combined titles.
> They are dropped simply by no longer matching any kept category (and the LLM
> rubric lists them as never-relevant).

---

## Exclusion List: Step 4 Refinement

If any exclusion keyword appears in the title (substring), exclude. Exclusions take priority over inclusions.

### Too Junior
> Note: Revenue Operations roles (Manager / Analyst / Specialist / Associate) are
> NO LONGER excluded — they are KEPT under Revenue (#2) at all levels. A plain
> "Operations Manager" is dropped by not matching any Head-of+ inclusion, not by an
> exclusion keyword (excluding "operations manager" would also kill kept titles).
- Project Manager
- Program Manager
- Assistant
- Assistante
- Office Manager
- Responsable Administratif et Financier
- Responsable Administrative et Financière

### Wrong C Suite
- CTO
- Chief Technology Officer
- Chief Technical Officer
- CPO
- Chief Product Officer
- CMO
- Chief Marketing Officer
- Chief Scientific Officer
- Chief Data Officer
- CDO
- Chief Information Officer
- CIO
- Chief of Staff

### Wrong Operations Type
> Sales Operations and Business Operations are NO LONGER excluded — their Head-of+
> variants are kept under Ops (#3). Only these wrong ops types are excluded:
- IT Operations
- Technical Operations
- Marketing Operations
- DevOps
- Dev Ops
- Back Office
- Responsable Back-Office
- Head of AI Operations
- Head of People Operations
- Head of Customer Operations
- Head of Product Operations

### Excluded Leadership Roles
- Managing Director
- Treasurer
- Trésorier
- Trésorière

### Deputy / Adjoint Roles
- Deputy
- Adjoint
- Adjointe
- Déléguée

### Board / Investors / Advisors
- Board Member
- Board Director
- Board Observer
- Board Secretary
- Board of Director
- Board of Directors
- Member Board
- Investor
- Private Investor
- Business Angel
- Advisor
- Adviser
- Conseil
- Administrateur
- Administratrice
- Independent Director
- Non-Executive
- Non Executive
- Mentor
- Scout

### Investment / Fund Keywords
- Venture Capital
- VC
- Private Equity
- PE Fund
- Fund Manager
- General Partner
- Operating Partner
- Portfolio Manager

### Consulting / Fractional Keywords
- Fractional
- Interim
- Consultant
- Freelance

### Project / Program Directors
- Project Director
- Directeur Projet
- Directeur de Projet
- Program Director
- Head of Project Management

---

## Priority Categories (for phone-cap ranking; cap applied in Step 5)

1. **Finance** : CFO, VP Finance, Head of Finance, DAF, FP&A (Controllers excluded)
2. **Revenue** : CRO, Chief Revenue Officer, Head/VP Revenue, CCO, Revenue Operations / RevOps (all levels)
3. **Ops** : COO, VP/Head/Director Operations, Head of Sales Ops, Head of Growth Ops, Head of Business Ops (Head-of+)

> Sales removed as a persona 2026-06-16 — see the note at the top of this file.
