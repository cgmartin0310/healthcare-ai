"""Unified CLI for the three-component Clinic Analyst slice."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analyst.banner import PRODUCT_BANNER
from analyst.engine import Analyst
from analyst.tenant import DEFAULT_TENANT, open_warehouse, parse_as_of, write_tenant_config
from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, load_mapping_json, propose_mapping, save_mapping_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clinic-analyst",
        description="Standalone clinic analyst (file ingest → PREP warehouse → grounded analyst).",
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT, help="Generic tenant id (not a Boom brand).")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD. Defaults to today / CLINIC_ANALYST_AS_OF.")
    parser.add_argument("--company", default=None, help="Optional Company filter.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_propose = sub.add_parser("propose", help="Propose a column mapping for a CSV/xlsx file.")
    p_propose.add_argument("file")
    p_propose.add_argument("--entity", choices=["APPOINTMENT", "REFERRAL", "PATIENT"])
    p_propose.add_argument("--out", required=True, help="Write mapping JSON here.")

    p_confirm = sub.add_parser("confirm", help="Human-confirm a proposed mapping.")
    p_confirm.add_argument("mapping")
    p_confirm.add_argument("--out", help="Write confirmed mapping (default: overwrite).")

    p_load = sub.add_parser("load", help="Load a confirmed mapping into the tenant warehouse.")
    p_load.add_argument("file")
    p_load.add_argument("--mapping", required=True)
    p_load.add_argument("--mode", choices=["replace", "append"], default="replace")

    p_ask = sub.add_parser("ask", help="Ask a grounded analyst question.")
    p_ask.add_argument("question")

    sub.add_parser("alerts", help="Evaluate wired user-defined alerts.")
    sub.add_parser("scheduled", help="Run scheduled-metrics hooks for daily/weekly/monthly cadence.")
    sub.add_parser("snapshot", help="Print the closed-month metric snapshot.")

    p_demo = sub.add_parser("demo", help="Map + load both synthetic layouts and ask sample questions.")
    p_demo.add_argument(
        "--fixtures",
        default=None,
        help="Synthetic fixture directory (defaults to ./fixtures/synthetic).",
    )

    args = parser.parse_args(argv)
    as_of = parse_as_of(args.as_of)

    if args.cmd == "propose":
        proposal = propose_mapping(args.file, entity=args.entity)
        save_mapping_json(proposal, args.out)
        print(json.dumps(proposal.to_dict(), indent=2))
        if proposal.unmapped_required:
            print("Unmapped required fields:", ", ".join(proposal.unmapped_required), file=sys.stderr)
            return 2
        return 0

    if args.cmd == "confirm":
        proposal = load_mapping_json(args.mapping)
        confirmed = confirm_mapping(proposal)
        save_mapping_json(confirmed, args.out or args.mapping)
        print(json.dumps(confirmed.to_dict(), indent=2))
        return 0

    if args.cmd == "load":
        proposal = load_mapping_json(args.mapping)
        with open_warehouse(args.tenant) as wh:
            counts = load_mapped_file(
                wh,
                args.file,
                proposal,
                tenant_id=args.tenant,
                mode=args.mode,
                manifest_path=Path(args.mapping).with_suffix(".manifest.json"),
            )
        print(json.dumps({"tenant": args.tenant, "loaded": counts, "banner": PRODUCT_BANNER}, indent=2))
        return 0

    if args.cmd in {"ask", "alerts", "scheduled", "snapshot"}:
        with open_warehouse(args.tenant) as wh:
            analyst = Analyst(wh, tenant_id=args.tenant, as_of=as_of, company=args.company)
            if args.cmd == "ask":
                print(format_answer(analyst.ask(args.question)))
            elif args.cmd == "alerts":
                print(json.dumps(analyst.alerts(), indent=2, default=str))
            elif args.cmd == "scheduled":
                print(json.dumps(analyst.scheduled(), indent=2, default=str))
            else:
                print(json.dumps(analyst.snapshot(), indent=2, default=str))
        return 0

    if args.cmd == "demo":
        fixtures = Path(args.fixtures) if args.fixtures else Path.cwd() / "fixtures" / "synthetic"
        return run_demo(fixtures, args.tenant, as_of)

    return 1


def format_answer(payload: dict) -> str:
    lines = [
        payload["banner"],
        "",
        f"Tenant: {payload['tenant_id']}  as_of={payload['as_of']}  "
        f"last_closed_month={payload['last_closed_month']['start']}–{payload['last_closed_month']['end']}",
        f"Intent: {payload['intent']}",
        "",
        payload["answer"],
    ]
    if payload.get("suggestions"):
        lines.append("")
        lines.append("Suggestions (from computed metrics only):")
        for s in payload["suggestions"]:
            lines.append(f"- {s}")
    return "\n".join(lines)


def run_demo(fixtures: Path, tenant_id: str, as_of) -> int:
    """Prove both synthetic layouts map and load; answer the locked sample questions."""
    print(PRODUCT_BANNER)
    print("SYNTHETIC EXAMPLE DATA — tied to no real clinic.\n")
    pairs = [
        (
            "APPOINTMENT",
            fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv",
            fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_visits.csv",
        ),
        (
            "REFERRAL",
            fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_referrals.csv",
            fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_incoming_referrals.csv",
        ),
        (
            "PATIENT",
            fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_patients.csv",
            fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_clients.csv",
        ),
    ]
    work = Path("./data/demo_mappings")
    work.mkdir(parents=True, exist_ok=True)
    write_tenant_config(
        tenant_id,
        {
            "tenant_id": tenant_id,
            "display_name": "Example Clinic (synthetic)",
            "synthetic_example": True,
        },
    )

    # Layout B is mapped into a second tenant to prove a differently-shaped dump loads.
    tenants = {0: tenant_id, 1: tenant_id + "-layout-b"}
    for layout_idx in (0, 1):
        tid = tenants[layout_idx]
        write_tenant_config(tid, {"tenant_id": tid, "display_name": "Example Clinic (synthetic)", "synthetic_example": True})
        with open_warehouse(tid) as wh:
            for entity, path_a, path_b in pairs:
                src = path_a if layout_idx == 0 else path_b
                mapping_path = work / f"{tid}_{entity}.json"
                proposal = propose_mapping(src, entity=entity)
                if proposal.unmapped_required:
                    print(f"FAIL propose {src}: missing {proposal.unmapped_required}")
                    return 2
                confirmed = confirm_mapping(proposal)
                save_mapping_json(confirmed, mapping_path)
                counts = load_mapped_file(
                    wh, src, confirmed, tenant_id=tid, mode="replace", manifest_path=mapping_path.with_suffix(".manifest.json")
                )
                mapped = [c for c in confirmed.columns if c.target_column]
                print(
                    f"Layout {'A' if layout_idx == 0 else 'B'} {entity}: "
                    f"proposed {len(mapped)} columns, loaded {counts}"
                )

    questions = [
        "Is cancelation over 25% in the last three months?",
        "Which payers have AR sitting past 30 days, by location?",
        "Referral-source drop-off — does volume support another therapist?",
        "How long does a new clinician take to fill a caseload?",
        "Which therapists are profitable after payroll?",
        "What can I do to improve my business?",
    ]
    with open_warehouse(tenant_id) as wh:
        analyst = Analyst(wh, tenant_id=tenant_id, as_of=as_of)
        print("\n=== Sample questions (layout A tenant) ===\n")
        for q in questions:
            print(f"Q: {q}")
            print(format_answer(analyst.ask(q)))
            print()
        print("=== Alerts ===")
        print(json.dumps(analyst.alerts(), indent=2, default=str))
        print("\n=== Scheduled hooks ===")
        print(json.dumps(analyst.scheduled(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
