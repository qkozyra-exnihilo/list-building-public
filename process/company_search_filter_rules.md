# Company Search Filter Rules: ICP Qualification

Single source of truth for ICP filtering across the list-building pipeline.

**Target (revised 2026-07-01):** B2B companies that monetize via **recurring or usage-based revenue** — SaaS (any vertical), usage/API/platform businesses, marketplaces with subscription/take-rate, fintech/insurtech, hardware+recurring-service, subscription services. The test is *"does it bill recurring/usage revenue?"*, not the narrower *"is it SaaS?"*. Vertical SaaS counts.

**Not target:** one-off retail, physical-goods sellers, project-based agencies/consultancies, in-person services with no recurring billing, professional services where software is incidental.

The pipeline has three complementary ICP gates:

| Gate | Step | Method | Primary purpose |
|---|---|---|---|
| **Primary** | Step 1d | Research-then-analyze LLM (`icp-research/icp_v2.py`) | Decide "does this company bill recurring/usage revenue?" from crawled web evidence |
| **Safety net** | Step 2 | NAICS exclusion + keyword rescue | Catch clear non-fit (restaurants, retail, construction…) that slipped past Step 1d |
| **Size gate** | Step 2 | Employee-count cap | Drop companies too big for the SMB/scaleup ICP |

---

## Step 1d — Research-then-analyze ICP classifier (primary gate)

**Module:** `~/github/lead-gen/list-building/icp-research/` (`icp_v2.py`). This **replaced** the old homepage-only Haiku classifier (`process/step1d_icp_homepage_check.py`), which flipped ~5% of borderline verdicts run-to-run (an ISP, an OSS framework, and a hardware co all passed once and failed on re-run). The redesign **collects context first, then analyzes it**, persisting the evidence in SQLite (`icp_context.db`) so verdicts are stable, cheap to re-score, and auditable.

### Two phases (over `icp_context.db`)
1. **`research`** — per company, crawl homepage + most-relevant internal pages (product/pricing/features/about/customers), strip shared nav/footer boilerplate, add pricing page + a Serper web-search snippet (~9k chars/company). Stored in the `context` table. Idempotent — skips already-fetched rows.
2. **`analyze`** — one structured-output call per company over the stored context → `verdict` table (`saas`/`not_saas`/`unclear`, confidence, vertical, reason, signals). Idempotent — skips already-scored rows.

CRM/Attio fields were tested as inputs and **removed** — they barely moved verdicts. Judge from web evidence only.

### ICP rule — TWO dimensions

A qualified target must satisfy both.

**Dimension 1 — Revenue model (product fit) — LIVE.** In-ICP = bills recurring/usage revenue (see Target above). A visible subscription/usage pricing page is a strong positive; **absence of a public pricing page is NEUTRAL** (enterprise/contact-sales companies have none) — infer the model from product category, billing language, named B2B customers, login/app/trial.

**Dimension 2 — Sales motion (qualification) — PENDING DATA (not yet enforced).** A recurring-revenue company is a *priority* target when it also has an active commercial motion: **sales team > 1** OR **actively hiring sales**. Sales-team size / hiring signals are sparse in Attio today (~15-18% coverage) and the env Apollo keys lack org-enrich access (403), so this dimension is applied only where `sales_headcount`/`hiring_sales_*` already exist. **Open next step: wire up sales-motion data retrieval.**

### Decision

| Verdict | Confidence | Action |
|---|---|---|
| `saas` | ≥ **0.80** | **KEEP** (in ICP) |
| `not_saas` | ≥ **0.80** | **DROP** (Not ICP) |
| anything else | any | **UNCERTAIN** (keep, manual review) |

Threshold is canonical at **0.80** (`PASS_THRESHOLD` in `icp_v2.py` and `attio_write_tier.py`; override via env `ICP_THRESHOLD`). Model: adaptive-thinking Opus for judgement quality, or Haiku 4.5 for cheap bulk passes (~$30 / 6.2k companies).

### Handoff to Step 1e (`icp_export.py`)

Verdicts live in `icp_context.db`; downstream Step 1e still consumes the old per-project files. `icp-research/icp_export.py <project> --input <csv> --domain-col <col>` joins the project's list against the DB and writes `{project}_step1d_classification.csv` + `{project}_step1d_icp_filtered.csv` (input minus the `not_saas≥0.80` DROPs) — the filtered file feeds Step 1e directly. Companies with no DB verdict are **kept** (never dropped) and flagged; run `icp_v2.py both` on the project list first to cover them.

### Why this design

- Keyword scoring (original approach) hit **55% pass rate** on Hyperline's real customers — way too low.
- Homepage-only Haiku classifier hit ~86%+9% review but was **non-deterministic** on borderline cases.
- Research-then-analyze + broadened recurring/usage rubric at 0.80: customer-recall validation on 111 clients rose **68% → 86%**.

---

## Step 2 — NAICS Exclusion + Keyword Rescue (safety net)

Runs after Apollo enrichment. Drops companies only when the NAICS code indicates a sector that is **structurally not SaaS**, AND there is no positive SaaS signal in the company's Apollo `keywords`.

### NAICS exclusion list

Only codes for business models that cannot themselves be SaaS.

| NAICS prefix | Sector |
|---|---|
| 11 | Agriculture, Forestry, Fishing, Hunting (production) |
| 21 | Mining, Quarrying, Oil & Gas Extraction |
| 23 | Construction (physical building) |
| 44 / 45 | Retail Trade |
| 72 | Accommodation & Food Services (restaurants, hotels) |
| 81211 | Hair, Nail, Skin Salons |
| 81221 | Funeral Services |
| 81232 | Industrial Launderers |
| 81293 | Parking Lots & Garages |
| 713 | Amusement / Gambling / Recreation (physical venues) |
| 71213 | Historical Sites |
| 62441 | Child Day Care Services |
| 7224 | Drinking Places |

**Kept narrow on purpose.** Vertical-SaaS operating codes (fintech 52xxx, healthtech 62199, legaltech 54111, logistics tech 49xxx, etc.) are NOT excluded — those map to legitimate Hyperline customers.

### Keyword rescue

A company whose NAICS matches the exclusion list is **rescued and kept** if its Apollo `keywords` contain any of these positive signals:

```
saas, ai, software, cloud, platform, api, analytics, automation, artificial intelligence
```

This catches vertical SaaS whose NAICS reflects their *customer industry* rather than their own business model. Examples:
- **Gastronaut** (NAICS 72251 — restaurants) rescued by `saas; platform` keywords
- **Citron** (NAICS 81221 — funeral) rescued by `saas; software for funeral homes` keywords
- **Sunday** (NAICS 72251) rescued by `payments; saas` keywords

### Supplementary keyword drops

Applied when Apollo `keywords` are present and clearly non-SaaS:

- **Consulting-only firms**: drop if "consulting" appears AND no positive SaaS signal
- **All-excluded keywords**: drop if every keyword is in the exclude list AND no positive SaaS signal

Exclude keywords: `non-profit`, `food`, `farming`, `maritime`, `sporting goods`.

---

## Step 2 — Company-Size Gate (employee-count cap)

Runs inside the Step 2 ICP filter, using Apollo's `estimated_num_employees`
(backfilled into the `employee_count` column during enrichment).

| Rule | Default | Flag |
|---|---|---|
| Drop if employees **> max** | **500** | `--max-employees N` (use `0` to disable) |
| Drop if employees **< min** | disabled | `--min-employees N` |

- **Unknown employee count → KEEP.** We never drop on missing data (Apollo
  misses ~10-15% of small/French companies); the homepage gate already vouched
  for them as SaaS.
- **Why 500:** Hyperline's outbound ICP is SMB/scaleup SaaS. Above ~500 employees
  the buying motion is enterprise (RFP, incumbent billing stack, procurement) and
  a poor fit for cold outbound. The cap is configurable per project.
- **Why it matters beyond fit:** big companies have far more finance/revenue
  people on LinkedIn, so without the cap they dominate the Pronto results. On
  `fr_valid_pricing_062026`, 6 companies >500 emp (Qonto, Mirakl, PayFit,
  Scaleway, ChapsVision, Stuart) produced 26 of 47 kept contacts (55%) before
  the gate was added.

---

## Combined pipeline behavior

On Hyperline's 133 real customers:

| Filter | Pass rate |
|---|---|
| Step 1d LLM classifier only | 94% (kept + review) |
| Step 2 NAICS exclusion only | 98% |
| Both combined | ~94% preserved, 6% manual review, 0% false-negative drops for vertical SaaS |

The old filter (industry allowlist + NAICS allowlist) passed only 32% — about two-thirds of real customers would have been incorrectly dropped.
