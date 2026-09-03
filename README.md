# healthcare-ai

Standalone clinic analyst: file-mapping integration engine, analysis warehouse, and AI analyst layer.

This is a product other clinics can use (ST/OT/PT groups and multi-site mental health), and a wedge for Paragon free billing reviews. It is **not** a rebuild of the Boom Therapy Slack bot, and it is **not** a Tableau replacement for Boom internal ops.

Christopher Martin locked the architecture as **three components**. This repo scaffolds all three as a working slice.

```
packages/
  integration_engine/   # one-time CSV/xlsx mapping onto PREP
  warehouse/            # DuckDB analysis store + locked metric functions
  analyst/              # grounded Q&A, alerts, scheduled-metric hooks
```

`packages/web` is a thin HTTP adapter for Render (`web.app:app`). It is not a fourth analysis component.

Closed-month results are the truth grain. Persistent banner:

> This product does not have a live future schedule or this-week book. Closed-month results are the truth grain.

## How to run

Python 3.11+. From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Synthetic example dumps (two column layouts, PHI-free, tied to no real clinic) already live under `fixtures/synthetic/`. Regenerate with `python scripts/generate_synthetic.py`.

Map, confirm, load, ask (as-of 2026-09-02 matches the fixture calendar):

```bash
clinic-analyst --tenant example-clinic deid fixtures/synthetic/layout_a/SYNTHETIC_EXAMPLE_appointments.csv
clinic-analyst --as-of 2026-09-02 demo
```

Or step by step:

```bash
clinic-analyst propose fixtures/synthetic/layout_a/SYNTHETIC_EXAMPLE_appointments.csv \
  --entity APPOINTMENT --out /tmp/appt.json
clinic-analyst confirm /tmp/appt.json
clinic-analyst --tenant example-clinic load \
  fixtures/synthetic/layout_a/SYNTHETIC_EXAMPLE_appointments.csv --mapping /tmp/appt.json

clinic-analyst propose fixtures/synthetic/layout_payments/SYNTHETIC_EXAMPLE_transactions.csv \
  --entity CLAIM_TXN --out /tmp/txn.json
clinic-analyst confirm /tmp/txn.json
clinic-analyst --tenant example-clinic load \
  fixtures/synthetic/layout_payments/SYNTHETIC_EXAMPLE_transactions.csv --mapping /tmp/txn.json --mode append

clinic-analyst --tenant example-clinic --as-of 2026-09-02 ask \
  "Is cancelation over 25% in the last three months?"
clinic-analyst --tenant example-clinic --as-of 2026-09-02 alerts
clinic-analyst --tenant example-clinic --as-of 2026-09-02 scheduled
```

### De-identification (import gate)

Prospects should assume **raw extracts are not stored**. This is a Safe Harbor-style gate, not ARX and not the OHDSI FHIR Anonymizer (wrong format; k-anonymity would destroy small-clinic metrics). It is **not** a legal determination that a file is de-identified. UI copy never says “HIPAA compliant” or “no HIPAA data.”

Before confirm/load of any CSV/xlsx (synthetic demo files included):

| Action | What |
|---|---|
| **Drop** (never mapped) | Likely PHI headers: name, first/last, address, street, city, zip, SSN, MRN, phone, email, account, member id, subscriber, insurance id, and DOB |
| **Hash** | `PatientId`, `ProviderId`, `ApptId`, `ReferralId`, `TxnId`, `ClaimId` — HMAC-SHA256 with a **per-tenant** secret at `{CLINIC_ANALYST_DATA_DIR}/tenants/{tenant_id}/deid.hmac` (on `/data`, never in git). Same raw id → same hash within a tenant so joins work. Different tenants → different hashes. |
| **Dates** | `ApptDate`, `PostedDate`, `DOS`, `DateTimeCreated` kept as month+year (day set to 1). `FirstInsPayment` is not generalized (days-to-pay needs a day). |
| **Age** | DOB is not stored. If DOB is present, import writes optional `AgeBand` (`Child` / `Adult`) for locked early-quit bars only. Not `AgeGroup`. |

A de-id **receipt** (dropped / hashed / generalized columns, row counts, no cell values) is shown after propose and written under `{data}/tenants/{tenant_id}/deid_receipts/`.

Local pre-send (clinic can inspect before upload). The server still re-runs the same gate:

```bash
clinic-analyst --tenant example-clinic deid path/to/export.csv
# writes path/to/export.deid.csv and path/to/export.deid.receipt.json
```

Notice on every receipt: “Safe Harbor identifiers are stripped or hashed before load. This is not a legal determination that the file is de-identified.”

Tests:

```bash
pytest -q
```

## Web app (Render)

A thin FastAPI wrapper (`web.app:app`) binds `0.0.0.0:$PORT`. Shared app, **isolated DuckDB per tenant** at `{CLINIC_ANALYST_DATA_DIR}/tenants/{tenant_id}/warehouse.duckdb`. Not Postgres. Not Snowflake.

Login is required. Signup creates a new tenant + first owner. Email+password, bcrypt hashes, HTTP-only signed session cookie. Users live in `{CLINIC_ANALYST_DATA_DIR}/auth.sqlite` on the same disk.

Documented **demo login** (PHI-free, not a production secret):

- email: `demo@example.clinic`
- password: `demo-clinic-2026`
- tenant: `example-clinic`

On startup, `seed_demo()` creates that user and, if the example-clinic warehouse has no APPOINTMENT rows, loads layout_a visits/referrals/patients plus layout_payments `CLAIM_TXN`. Idempotent. Demo chat works after login without clicking **Run synthetic demo**. When the client omits `as_of` for the demo tenant, the server uses **2026-09-02** (synthetic calendar; August stays closed). Other tenants still default to today / `CLINIC_ANALYST_AS_OF`. The as-of date field is on the chat form.

A second clinic can sign up and gets an empty warehouse that cannot see the demo tenant. If a tenant has no visits, the UI and analyst show: “No visits loaded yet — run synthetic demo or upload files”.

Local:

```bash
pip install -e ".[web]"
CLINIC_ANALYST_DATA_DIR=./data uvicorn web.app:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000 — log in, upload multiple files (visits, referrals, claim ledger), then **talk to the analyst** in the chat thread. Suggested chips are optional. Persistent banner: no live future schedule / this-week book.

### Chat (Grok tools)

After login the user types any ops/billing question. The analyst is this clinic's grounded bot:

- Closed-month truth. No live schedule. PHI = ids only. Never invent numbers. Never mix tenants.
- If `XAI_API_KEY` is set, answers go through xAI Grok (`https://api.x.ai/v1` chat completions, default model **grok-4.6** as published at [x.ai/api](https://x.ai/api); override with `XAI_MODEL`). The model may call locked metric tools (cancelation, churn, referrals, AR/`InsBalance`, avg paid, avg collections, days to pay, staffing forecast, caseload fill, snapshot, alerts) plus a read-only `SELECT` helper on this tenant's DuckDB only (`APPOINTMENT` / `PATIENT` / `REFERRAL` / `CLAIM_TXN`, row-capped). Prefer metric tools when the question matches a locked def.
- If `XAI_API_KEY` is missing, keyword routing still answers (so Render without a key works). The UI says chat-with-tools is off until the key is set.

Set the key in the Render dashboard (Blueprint prompts for `XAI_API_KEY` because `sync: false`). Do not commit the key.

Synthetic demo data (not Boom): Example Clinic, ~1800 visits, 80 patients, ~200 referrals, plus `CLAIM_TXN` claim-ledger rows (charges / payments). Tied to no real clinic.

### Deploy on Render

1. Push this branch (`cursor/clinic-analyst-first-pass-5759`).
2. Render Dashboard → **New** → **Blueprint** → this repo → **Apply**.
3. `render.yaml`: one web service, `uvicorn web.app:app --host 0.0.0.0 --port $PORT`, `/healthz`, disk `/data` (`CLINIC_ANALYST_DATA_DIR=/data`, generated `CLINIC_ANALYST_SECRET`). Set `XAI_API_KEY` in the dashboard when you want Grok tool-chat (Blueprint `sync: false` prompts on first apply).

No Snowflake credentials and no Postgres add-on. Users + tenant DuckDB files persist on `/data`.

## Environment

See `.env.example`. Local state is `CLINIC_ANALYST_DATA_DIR` (default `./data`). No secrets belong in the repo. `XAI_API_KEY` enables Grok tool-chat; without it the regex fallback still answers from locked metrics.

## The three components

### 1. Integration engine

v1 is **one-time file ingest** (CSV/xlsx) with an agent in the loop: propose a column mapping onto PREP `APPOINTMENT` / `REFERRAL` / `PATIENT` / optional `CLAIM_TXN` (claim ledger), human confirm, then load. Multiple files per tenant (schedule, referrals, charges, payments, adjustments, aging). Charge files map onto `CLAIM_TXN` the same as payment files — there is no separate `CHARGES` table. A tenant can load APPOINTMENT only.

- Does **not** log into client EHRs, scrape portals, or run a live ongoing feed.
- Every CSV/xlsx goes through a **de-identification gate** before confirm/load (and again on ingest). See below.
- The proposer is synonym + fuzzy + type-hint scoring. Confirm is mandatory before load.

### 2. Analysis warehouse

Shaped after Clinic Analyst Snowflake `BOOMREPORTING.PREP` so locked metric defs can be reused. Identifiers are mixed-case and **must be quoted** (e.g. `"ApptDate"`).

Local/dev store is **DuckDB** (columnar, simple, fast). The same quoted identifiers are the documented path to Snowflake (`warehouse/snowflake.py` shows the cancelation SQL). Do not point a demo tenant at Boom live data.

Core entities: `APPOINTMENT`, `REFERRAL`, `PATIENT`, plus optional `CLAIM_TXN` (claim ledger: charges / payments / allowances / adjustments / refunds; money source of truth when present). When `CLAIM_TXN` is present, locked money metrics derive `TotalPaid` / `InsPaid` / `InsBalance` / `FirstInsPayment` from it (do not require mapping `FirstInsPayment`). When absent, appointment rollup columns are used. If neither, the analyst says the data is not in the dump. Rendering clinician is `ProviderId` / `ProviderName`.

Boom ClinicId → Company is for schema fidelity only and is **not** shown as the product: `8=CST`, `9=AOT`, `22=KID`, `24=PTA`. Demo tenants are generic clinics (`Example Clinic`).

`PATIENT.PatientActive` is **not** operationally active.

Payments use `TotalPaid`. AR/collections use `InsPaid`. Do not mix.

### 3. AI analysis layer

The user talks to the analyst in free text, like a Grok bot — not canned buttons. With `XAI_API_KEY`, Grok calls the locked metric functions as tools. Without a key, a keyword router still maps common questions onto those same functions. Answers are grounded: every number comes from the tools / warehouse. If the dump cannot support a question (payroll, caseload fill), the analyst says the data is not there.

Wired user-defined alerts:

1. Cancelation over 25% in the last 3 closed months
2. Referral volume −10% vs the prior closed month
3. Early quit watch (cancelation > 30% under the locked tenure bars)

Scheduled-metrics hooks accept daily / weekly / monthly cadence config (`analyst.schedule`).

Sample questions the slice actually answers from mapped data:

- Which payers have AR sitting past 30 days, by location?
- Is cancelation over 25% in the last three months?
- Referral-source drop-off / does volume support another therapist?
- How long does a new clinician take to fill a caseload? (only if Completes support it)
- Which therapists are profitable after payroll? (only if payroll is in the dump — it is not)
- What can I do to improve my business?

## Locked metric glossary

| Metric | Definition |
|---|---|
| Completes | `AppointmentStatus='Complete'` (`Status='Complete'`) |
| Cancelation % | `(Cancelled + No Show) / (Complete + Cancelled + No Show)`. Pending/Waiting out. |
| Active book | ≥1 Complete in the calendar month. **Not** `PATIENT.PatientActive`. |
| Churn | Grain = Company × Discipline × PatientId. Prior = month before last closed month; current = last closed month. Drop first-DOS on/after prior month start. Churned = prior active, not current active. New patients never enter the prior cohort. Closed months only. |
| Early quit watch | Cancelation % > 30% while under tenure bar: PT / adult OT-ST < 3 months; child OT-ST < 6. DFlex % is not a quit warning. |
| Primary payer | Visit-level: `APPOINTMENT.PrimaryPayorName`. Patient-level: latest Complete in the window (`ApptDate DESC`, `ApptId DESC`). |
| Referrals | `COUNT` of `REFERRAL` rows. Conversion = converted / referrals. Converted = `"Completed?"=1`. EVAL is eval notes, not conversion. |
| Avg Collections | `InsPaid` by payer, DOS=`ApptDate`, window start 60 days ago going back 3 months, includes zeros/partials. |
| Avg Paid | `InsPaid>0` only, last 3 months through as-of. |
| Days to pay | `DATEDIFF(day, ApptDate, FirstInsPayment)` on Completes with `InsPaid>0`, exclude negatives, min 20 claims. |
| Payments vs AR | Payments = `TotalPaid`. AR/collections = `InsPaid`. |
| Dollar AR aged > 30 | `SUM(InsBalance)` on Completes where `InsBalance > 0` and `ApptDate` aged > 30 days, split by `PrimaryPayorName` × `LocationName`. Insurance only. Not billed − paid (no charge). Not `PatBalance`. Not Tableau NET AR. Expected-recovery (`InsPaid` × open-claim count) is a separate question. |

Staffing working model (when the analyst forecasts FTE):

- OT/PT 35 visits/week, ST 70
- Patients OT/PT 1×/week, ST 1.5×
- GM revenue $95 OT/PT, $67 ST
- Conversion planning 50%
- Rounding is a tenant setting; default nearest 0.5, min 1
- Thin-data churn plugs: prior-active < 20 → OT/ST 10%, PT 20%
- Demand next month = last closed month Completes × (1 − clinic monthly churn) + (refs/mo × 50% conversion × 52/12 × visits-per-new)
- Headcount = unique `ProviderId` (fallback `ProviderName`) with ≥1 Complete in last closed month, one primary location. Boom `TherapistName` maps onto `ProviderName`. There is no `TherapistId`.
- Forecast churn is clinic×discipline, not therapist-level

## Schema notes (no full DDL dump)

Column remaps onto PREP (not new metrics):

- `APPOINTMENT.LocationName` (not `Location`). Rendering clinician is `ProviderId` + optional `ProviderName`. Synthetic layout_a includes `ProviderId` (PRV01…) and keeps `TherapistName` as the display synonym that maps to `ProviderName` — it is not dropped as patient PHI. Opening a warehouse copies leftover `TherapistName` into `ProviderName` and adds `ProviderId` if missing. Demo reload replace-drops tables so an old schema cannot linger. Optional `CPT` and `SecondaryPayorName` (COB). Do not add `CurrentPayer` or a coverage table.
- Company is stamped from the logged-in tenant when an upload omits it. Do not require `Company` in every file.
- `REFERRAL.Source` (often blank). KID dumps often have PCP Name; that is not the generic source field. Do **not** use `REFERRAL_SOURCES."Org Name"` (CST-only).
- `PATIENT.DOB` is **not stored**. At import, DOB (if present) becomes optional `AgeBand` (`Child` / `Adult`, child = age < 18 at as-of). There is no `AgeGroup` warehouse column. Locked early-quit bars are unchanged: PT / adult OT-ST < 3 months; child OT-ST < 6.
- `APPOINTMENT.InsBalance` — dollar AR aged > 30 days lands here (`SUM` on Completes, `InsBalance > 0`, `ApptDate` aged > 30 days, `PrimaryPayorName` × `LocationName`, insurance only). Not billed − paid. Not `PatBalance`. Not Tableau NET AR.
- Optional `CLAIM_TXN` (claim ledger when present — not payments-only; no separate `CHARGES` table): required `TxnId`, `ApptId`, `PatientId`, `Company`, `PostedDate`, `Payer`, `TxnType` (`charge|allowance|payment|adjustment|refund`), `Amount`. Optional `LocationName`, `Discipline`. Charge files map here the same as payment files. When present, locked money metrics **derive** `TotalPaid` / `InsPaid` / `InsBalance` / `FirstInsPayment` from it. When absent, appointment rollup columns are used if present. If neither exists, the answer is that the data is not in the dump. Do not add `PatBalance`.
- `APPOINTMENT.Telehealth` is stored for schema fidelity. There is no locked telehealth metric, so none is computed.
- Payroll is not a PREP object. Therapist profitability after payroll is refused.

Payments stay `TotalPaid`. AR/collections stay `InsPaid` except the `InsBalance` landing for dollar AR aged > 30 days.

## Non-goals

- EHR login or portal scraping
- Live ongoing integrations
- New competing metric definitions
- Mixing Boom live data into a demo tenant
- Invented numbers
- Automated patient/parent texts
- Secrets in the repo
- Patient names, addresses, or claim lists as default product views
- Live future book, this-week vs last-week schedule, waitlist placement, slots engine

## Layout

```
packages/integration_engine/src/integration_engine/
packages/warehouse/src/warehouse/
packages/analyst/src/analyst/
packages/web/src/web/            # FastAPI adapter (Render); not a fourth metric layer
fixtures/synthetic/layout_a/     # PREP-like visit/referral/patient headers
fixtures/synthetic/layout_b/     # different export headers
fixtures/synthetic/layout_payments/  # CLAIM_TXN claim-ledger rows (charges / payments)
tests/                           # locked-def, auth isolation, CLAIM_TXN, e2e
render.yaml                      # one web service, DuckDB + users on /data
```
