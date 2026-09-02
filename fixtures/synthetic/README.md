# SYNTHETIC example dumps

These files are **example data, tied to no real clinic**. They contain id-only
or obviously fake values. They are not Boom CST/AOT/KID/PTA data and must not
be treated as a client billing extract.

Two column layouts of the same semantic tenant exist so the integration engine
can prove mapping onto PREP `APPOINTMENT`, `REFERRAL`, and `PATIENT`.

- `layout_a/` — PREP-like headers (`ApptDate`, `LocationName`, `InsBalance`, `Source`, `DOB`)
- `layout_b/` — a different export shape (`date_of_service`, `site`, `insurance_balance`, `source`, `dob`)

Regenerate with:

```bash
python scripts/generate_synthetic.py
```
