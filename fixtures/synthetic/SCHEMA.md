# Warehouse schema reference (not a sample export)

This is the PREP-shaped analysis grain. **Do not ship this as a clinic dump.**
Sample files under `profiles/` use practice-management / billing dialects and
must be mapped onto these names.

| Table | Grain |
|---|---|
| `APPOINTMENT` | one visit |
| `REFERRAL` | one referral row (count = referrals in) |
| `PATIENT` | Company × PatientId (DOB is never stored) |
| `CLAIM_TXN` | one claim-ledger row (charge / allowance / payment / adjustment / refund) |

`TxnType` stays `charge|allowance|payment|adjustment|refund`. There is no
`CHARGES` table. Company is overwritten from the logged-in tenant on load.

Locked metric definitions are unchanged. See the repo README.
