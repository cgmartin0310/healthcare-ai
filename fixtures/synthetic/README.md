# SYNTHETIC example dumps

These files are **example data, tied to no real clinic**. They contain id-only
or obviously fake values. They are not Boom CST/AOT/KID/PTA data and must not
be treated as a client billing extract.

Two visit-layout variants of the same semantic tenant exist so the integration
engine can prove mapping onto PREP `APPOINTMENT`, `REFERRAL`, and `PATIENT`. A
third **payments** file maps onto optional `CLAIM_TXN`. A tenant can load visits
only; missing REFERRAL or CLAIM_TXN is not a failed load.

- `layout_a/` — PREP-like headers (`ApptDate`, `LocationName`, `InsBalance`, `Source`, `DOB`)
- `layout_b/` — a different export shape (`date_of_service`, `site`, `insurance_balance`, `source`, `dob`)
- `layout_payments/` — charge/payment rows (`txn_id`, `visit_id`, `posted_on`, `txn_type`, `amount`) → `CLAIM_TXN`

When `CLAIM_TXN` is loaded, locked money metrics derive `TotalPaid` / `InsPaid` /
`InsBalance` / `FirstInsPayment` from those rows. When it is absent, appointment
rollup columns are used. Cancelation, churn, and conversion stay on raw
`APPOINTMENT` / `REFERRAL` and do not change.

Documented demo login (PHI-free, not a production secret): `demo@example.clinic`
/ `demo-clinic-2026` → tenant `example-clinic`. A second clinic can sign up and
gets an empty isolated warehouse.

Regenerate with:

```bash
python scripts/generate_synthetic.py
```
