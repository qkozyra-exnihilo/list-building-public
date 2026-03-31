# Company Search Filter Rules: ICP Qualification

Single source of truth for Step 2 company filtering.

<!-- Define your own target vertical and filter criteria below -->
**Target:** [Your target vertical / company type here]
**Applied:** after Apollo enrichment (Step 2)
**Uses 3 layers:** industry, NAICS, and Apollo company keywords

---

## Layer 1: Industry (from Apollo `industry` field)

Primary filter. Match is case insensitive.

Target industries:
<!-- Replace with your own target industries from Apollo's industry taxonomy -->
[Your target industries here]

Example format:
- industry name 1
- industry name 2
- industry name 3

---

## Layer 2: NAICS (secondary catch)

Keep if naics_code starts with any of these prefixes.
Both NAICS 2017 and 2022 revisions are accepted (Apollo may return either).

<!-- Replace with NAICS prefixes relevant to your target vertical -->
<!-- Find codes at https://www.census.gov/naics/ -->

| Prefix | Description                                  |
|--------|----------------------------------------------|
| [Your target NAICS codes here] | [Description] |

Example format:
| 5112   | Software Publishers                         |
| 5182   | Data Processing, Hosting                    |
| 5415   | Computer Systems Design & IT Services       |

---

## Layer 3: Keyword confirmation (AND operator on Layer 1/2)

Used to confirm that a company matching industry or NAICS is actually in your target vertical.
Checks the Apollo `keywords` field.

### Include keywords
At least one must appear. If none appear and no exclude triggers, see fallback.

<!-- Replace with keywords that confirm a company is in your ICP -->
[Your include keywords here]

Example:
- keyword1
- keyword2
- keyword3

### Exclude keywords
If ONLY these appear (no include keyword present), drop.

<!-- Replace with keywords that disqualify a company -->
[Your exclude keywords here]

Example:
- non-profit
- irrelevant-industry-term

### Special rule: "consulting"
- "consulting" + at least one include keyword = **KEEP** (target company that also consults)
- "consulting" + NO include keyword = **DROP** (real consulting firm, not target vertical)

---

## Filter Logic (applied in Step 2 after Apollo enrichment)

A company is **KEPT** if:
1. (Industry OR NAICS matches) AND has at least one include keyword
2. (Industry OR NAICS matches) AND has no keywords in DB (can't verify, keep)
3. No industry AND no NAICS in DB (can't verify, keep for manual review)

A company is **DROPPED** if:
- Neither industry nor NAICS matches
- Industry or NAICS matches BUT keywords only contain exclude terms (no include)
- Industry or NAICS matches BUT keywords only contain "consulting" (no include)
