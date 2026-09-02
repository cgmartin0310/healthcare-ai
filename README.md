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
clinic-analyst --as-of 2026-09-02 demo
```

Or step by step:

```bash
clinic-analyst propose fixtures/synthetic/layout_a/SYNTHETIC_EXAMPLE_appointments.csv \
  --entity APPOINTMENT --out /tmp/appt.json
clinic-analyst confirm /tmp/appt.json
clinic-analyst --tenant example-clinic load \
  fixtures/synthetic/layout_a/SYNTHETIC_EXAMPLE_appointments.csv --mapping /tmp/appt.json

clinic-analyst --tenant example-clinic --as-of 2026-09-02 ask \
  "Is cancelation over 25% in the last three months?"
clinic-analyst --tenant example-clinic --as-of 2026-09-02 alerts
clinic-analyst --tenant example-clinic --as-of 2026-09-02 scheduled
```

Tests:

```bash
pytest -q
```

## Environment

See `.env.example`. Local state is `CLINIC_ANALYST_DATA_DIR` (default `./data`). No secrets belong in the repo. An optional `OPENAI_API_KEY` is reserved for later prose rewrite of **already computed** facts and is not used as a source of numbers.

## The three components

### 1. Integration engine

v1 is **one-time file ingest** (CSV/xlsx) with an agent in the loop: propose a column mapping onto PREP `APPOINTMENT` / `REFERRAL` / `PATIENT`, human confirm, then load.

- Does **not** log into client EHRs, scrape portals, or run a live ongoing feed.
- PHI stays out of the extract where possible. Default views use ids, not patient names, addresses, or claim lists.
- The proposer is synonym + fuzzy + type-hint scoring. Confirm is mandatory before load.

### 2. Analysis warehouse

Shaped after Clinic Analyst Snowflake `BOOMREPORTING.PREP` so locked metric defs can be reused. Identifiers are mixed-case and **must be quoted** (e.g. `"ApptDate"`).

Local/dev store is **DuckDB** (columnar, simple, fast). The same quoted identifiers are the documented path to Snowflake (`warehouse/snowflake.py` shows the cancelation SQL). Do not point a demo tenant at Boom live data.

Core entities only: `APPOINTMENT`, `REFERRAL`, `PATIENT`. No extra warehouse objects beyond what locked metrics need.

Boom ClinicId → Company is for schema fidelity only and is **not** shown as the product: `8=CST`, `9=AOT`, `22=KID`, `24=PTA`. Demo tenants are generic clinics (`Example Clinic`).

`PATIENT.PatientActive` is **not** operationally active.

Payments use `TotalPaid`. AR/collections use `InsPaid`. Do not mix.

### 3. AI analysis layer

Trained on the locked metric functions as the starting point. The user asks questions like an analyst. Answers are grounded: every number comes from those functions. If the dump cannot support a question (payroll, caseload fill), the analyst says the data is not there.

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

Staffing working model (when the analyst forecasts FTE):

- OT/PT 35 visits/week, ST 70
- Patients OT/PT 1×/week, ST 1.5×
- GM revenue $95 OT/PT, $67 ST
- Conversion planning 50%
- Rounding is a tenant setting; default nearest 0.5, min 1
- Thin-data churn plugs: prior-active < 20 → OT/ST 10%, PT 20%
- Demand next month = last closed month Completes × (1 − clinic monthly churn) + (refs/mo × 50% conversion × 52/12 × visits-per-new)
- Headcount = unique `TherapistName` with ≥1 Complete in last closed month, one primary location
- Forecast churn is clinic×discipline, not therapist-level

## Schema notes (no full DDL dump)

No second metric model was invented. A few PREP column *names* are inferred because locked metrics or sample questions need them and no official dump lists them:

- `PATIENT.AgeGroup` — child vs adult, required for the locked early-quit tenure bars. If Boom PREP uses another name, remap; do not change the bars.
- `APPOINTMENT.Location`, `TherapistName`, `REFERRAL.ReferralSource` — required to answer AR-by-location, headcount, and referral-source drop-off.
- `APPOINTMENT.Telehealth` is stored for schema fidelity. There is no locked telehealth metric, so none is computed.
- **Dollar AR (billed − paid) cannot land.** PREP as described has `InsPaid` / `FirstInsPayment` / `TotalPaid`, not a charge. The analyst reports Completes aged > 30 days with unpaid insurance, by payer × location, and says so. Self-pay is excluded (not insurance AR).
- Payroll is not a PREP object. Therapist profitability after payroll is refused.

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
fixtures/synthetic/layout_a/     # PREP-like headers
fixtures/synthetic/layout_b/     # different export headers
tests/                           # locked-def tests + e2e mapper/analyst
```
