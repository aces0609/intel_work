# intel_work: touch-labor site screening

Fact-finding on whether 10 candidate locations make sense for manufacturing centers:
population, education, wages, and the large employers already competing for hourly labor.

"Touch labor" = hands-on production work (assemblers, machinists, welders, inspectors) plus the
lower-skill hourly pool manufacturing recruits from or loses workers to (retail, warehouse and
distribution, material movers).

Outputs: `output/touch_labor_comparison.xlsx` and `MEMO.md`.

## Run

```bash
uv --directory <repo> run python -m touch_labor.build
```

Inputs that must be in `data/raw/` (gitignored; bls.gov blocks scripted downloads, so save them from a browser):

- `area_definitions_m2025.xlsx` from https://www.bls.gov/oes/area_definitions_m2025.xlsx
- `oesm25ma/MSA_M2025_dl.xlsx` and `oesm25ma/BOS_M2025_dl.xlsx` from https://www.bls.gov/oes/special-requests/oesm25ma.zip

Census gazetteers and the IPEDS directory download automatically. ACS comes from Census Reporter's public API.
County Business Patterns needs a free `CENSUS_API_KEY` (env var or `.env`); without it that sheet is skipped.

## Layout

- `locations.yaml`: the 10 sites, FIPS codes, and assumptions (two requested names looked like typos)
- `src/touch_labor/`: `geo` (labor shed), `census` (ACS, CBP), `bls` (OEWS wages), `education` (IPEDS), `build` (workbook)
- `data/employers_*.csv`: researched large employers, one source URL per row
