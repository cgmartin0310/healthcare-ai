# SYNTHETIC example dumps

These files are **example data, tied to no real clinic**. They are labeled
`SYNTHETIC_EXAMPLE`. They are not Boom CST/AOT/KID/PTA extracts.

Three clinic profiles, each in a different export dialect (never warehouse
headers). Each profile has visits, referrals, patients, and a claim ledger
(charges **and** payments in one `CLAIM_TXN`-shaped file):

- `profiles/harbor_pediatric/` — Harbor Pediatric Therapy (peds OT/ST, 2 sites)
- `profiles/riverbend_pt/` — Riverbend Physical Therapy (adult PT, 3 sites)
- `profiles/northside_bh/` — Northside Behavioral Health (telehealth-heavy)

Raw files include fake names, DOB, phone, email, address, MRN, and member id
so the Safe Harbor import gate has something to drop. After load the warehouse
holds ids only. This is not a legal HIPAA determination.

`TxnType` stays `charge|allowance|payment|adjustment|refund`.

Regenerate (parameterized — edit `PROFILES` in the script to retune volumes):

```bash
python scripts/generate_synthetic.py
```

Schema reference (not a sample file): `SCHEMA.md`.
