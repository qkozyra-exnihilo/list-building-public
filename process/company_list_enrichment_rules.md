# Company List Enrichment Rules

Applied during Step 1, before contact search.

---

## Required Columns
Every company list should have at minimum:
- company_name
- domain

## Optional Columns (from source list)
- sector (free text): include if provided, not required

## Industry Classification Codes
Add industry codes to improve segmentation and filtering.
Look up and add whichever code is findable. Skip if neither is available.

### NAICS (North American Industry Classification System)
- Use for: US companies, global companies, or when NACE is not available
- Format: 5 6 digit code (e.g. 51321 = Software Publishers)
- Lookup sources:
    - https://www.census.gov/naics/
    - Apollo company data often includes NAICS
    - Clearbit / Apollo API response may return naics_codes field

### NACE (Nomenclature des Activités Économiques en Communauté Européenne)
- Use for: French and European companies
- Format: Letter + 4 digits (e.g. J62.01 = Computer programming)
- Lookup sources:
    - https://ec.europa.eu/eurostat/ramon/nomenclatures/
    - INSEE (for French companies): https://www.insee.fr/fr/information/2406147
    - Societe.com often shows NAF/NACE code for French companies
    - Apollo may return sic_codes which can be cross referenced

### Priority Logic
1. If company is French, prefer NACE (also called NAF code in France)
2. If company is US or global, prefer NAICS
3. If both available, include both columns
4. If neither found, leave blank, do not block the enrichment process

## Target Output Columns (Step 1)
This is the company list that will feed into Step 2 (Apollo) and Step 3 (Pronto).

| Column               | Required | Description                                     |
|----------------------|----------|-------------------------------------------------|
| company_name         | Yes      | Official company name                           |
| domain               | Yes      | Primary website domain                          |
| linkedin_company_url | Yes      | LinkedIn company page URL (filled by Apollo)    |
| nace_code            | No       | NACE/NAF code if available                      |
| nace_label           | No       | NACE description label                          |
| industry             | No       | Apollo industry label (filled by Step 2)        |
| naics_code           | No       | NAICS code (5 6 digits, filled by Step 2)       |
| naics_label          | No       | NAICS description label                         |
| sector               | No       | Free text sector from source list (if provided) |

Note: linkedin_company_url is filled during Step 2 (Apollo enrichment), not Step 1.

## Notes
- NACE codes in France are also referred to as "code APE" or "code NAF"
- Apollo bulk enrichment (/api/v1/organizations/bulk_enrich) sometimes returns industry codes in the response. Always check the raw response for these fields
- If doing a large list, prioritize finding codes for the biggest companies first
