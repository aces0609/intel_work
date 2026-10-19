# Active job postings pipeline

Pulls currently open jobs from employers' own career sites near each of the 10 sites, keeps a history,
and feeds the workbook. All sources are public endpoints that the companies' career pages call
themselves. Nothing here logs in, solves a CAPTCHA, or works around bot protection; a source that
blocks scripts is marked unsupported instead.

## Run it (repeatable)

```bash
uv --directory <repo> run python -m touch_labor.jobs run            # fetch everything, record history
uv --directory <repo> run python -m touch_labor.jobs run --sites palmdale_ca --companies Walmart
uv --directory <repo> run python -m touch_labor.jobs run --exclude Walmart "The Home Depot" Amazon
uv --directory <repo> run python -m touch_labor.jobs report         # counts per site and company
uv --directory <repo> run python -m touch_labor.jobs sources        # what is covered / skipped and why
uv --directory <repo> run python -m touch_labor.build               # refresh the workbook (adds jobs sheets)
```

A full run takes a while (Walmart is deliberately paced at one request a second). Weekly is a sensible
cadence; schedule the `run` command with Windows Task Scheduler or any scheduler you use.

## How it works

1. `data/job_sources_*.yaml` list the companies per site and how to read each one (platform, config,
   per-site `search_terms`). `job_sources_core.yaml` holds Walmart, Home Depot and Amazon (every site);
   the other files hold the regional lists, each entry marked `verified` only if a live request returned data.
2. `runner.py` runs each (company, site) scope through its connector (`connectors.py`,
   `connectors_other.py`) and normalizes postings (`models.py`): title, location, pay when stated,
   a rough labor class (production / warehouse / retail / professional), posted date.
3. `store.py` upserts them into `data/jobs/jobs.db` (SQLite, not committed). A posting is keyed on
   company + site + id; each successful scope stamps `last_seen` and closes anything it no longer sees.
   A failed scope changes nothing, so an outage never mass-closes postings.
4. After each run a dated snapshot of active postings is written to `data/jobs/snapshots/` (committed),
   so history survives without the database file.
5. `report.py` builds the per-site / per-company tables used by the CLI and `build.py`.

## Adding a company

Add an entry to a `data/job_sources_*.yaml` file:

```yaml
- company: Example Co
  platform: workday            # workday | greenhouse | lever | smartrecruiters | eightfold | other
  config: {tenant: example, wd: wd5, site: ExampleCareers}
  sites: [clearfield_ut]
  search_terms: {clearfield_ut: [Clearfield, Layton, Ogden]}
  verified: true
  hourly_relevance: high
```

`platform: other` is for Oracle Recruiting Cloud, Phenom, Jibe, UKG, Teamtailor, ApplicantPro, ADP and
Paylocity; the kind is inferred from the config text (see `other_kind`). Check it with
`run --companies "Example Co"` before setting `verified: true`.

## Known limits

- **Not covered**: FedEx, Tesla, General Atomics (BrassRing), iCIMS and Avature portals, USAJOBS (needs a
  key) and some others, because they block scripts or have no public endpoint. `sources` lists each with a reason.
- **Amazon**: only amazon.jobs; hourly fulfillment-center roles are on hiring.amazon.com, which is not read.
- **Walmart**: its API does not give job URLs; postings link to the search page. Walmart throttles fast
  clients (HTTP 444), hence the slow pacing and retries.
- **Location matching** on platforms without a radius search is by city text plus state, using the
  `search_terms` in the YAML. Postings listed as "N Locations" cannot be placed and are skipped.
  Overlapping sites (Clearfield and Corinne share Ogden) can show the same posting under both.
- **Labor class** is a keyword guess from the job title; treat it as a guide.
- A posting count is demand, not headcount: one posting can cover several openings, and some
  postings are evergreen.
