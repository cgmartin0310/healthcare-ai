"""Grounded analyst. Numbers come only from locked warehouse metrics."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

from analyst.alerts import AlertDef, evaluate_alerts
from analyst.banner import PRODUCT_BANNER
from analyst.schedule import run_scheduled
from warehouse.dates import last_closed_month
from warehouse.metrics import (
    ar_past_30_days,
    avg_collections,
    avg_paid,
    cancelation_rate,
    caseload_fill,
    churn,
    days_to_pay,
    payroll_present,
    referral_volume_change,
    referrals,
    snapshot,
)
from warehouse.staffing import forecast
from warehouse.store import Warehouse

from analyst.llm import complete_chat, llm_available, parse_tool_args, system_prompt, tools_notice, xai_model
from analyst.tools import TOOL_SCHEMAS, dump_tool_result, run_tool

EMPTY_WAREHOUSE = "No visits loaded yet — run synthetic demo or upload files"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a (insufficient denominator)"
    return f"{value:.1%}"


class Analyst:
    def __init__(self, warehouse: Warehouse, *, tenant_id: str, as_of: date, company: str | None = None):
        self.warehouse = warehouse
        self.tenant_id = tenant_id
        self.as_of = as_of
        self.company = company

    def ask(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        use_tools: bool | None = None,
    ) -> dict[str, Any]:
        q = question.strip()
        if self.warehouse.count("APPOINTMENT") == 0:
            return {
                "banner": PRODUCT_BANNER,
                "tenant_id": self.tenant_id,
                "as_of": self.as_of.isoformat(),
                "last_closed_month": {
                    k: v.isoformat() for k, v in zip(("start", "end"), last_closed_month(self.as_of))
                },
                "question": q,
                "intent": "empty_warehouse",
                "mode": "empty",
                "tools_called": [],
                "tools_notice": tools_notice(),
                "answer": EMPTY_WAREHOUSE,
                "evidence": {"appointment_rows": 0},
                "suggestions": [],
                "grounded": True,
                "empty_warehouse": True,
            }
        if use_tools is None:
            use_tools = llm_available()
        if use_tools:
            try:
                return self._ask_with_tools(q, history or [])
            except Exception as exc:
                body = self._ask_regex(q)
                body["mode"] = "fallback"
                body["tools_notice"] = f"Chat-with-tools failed ({exc}). Using keyword routing."
                return body
        return self._ask_regex(q)

    def _ask_regex(self, q: str) -> dict[str, Any]:
        handler, intent = self._route(q)
        body = handler(q)
        return {
            "banner": PRODUCT_BANNER,
            "tenant_id": self.tenant_id,
            "as_of": self.as_of.isoformat(),
            "last_closed_month": {k: v.isoformat() for k, v in zip(("start", "end"), last_closed_month(self.as_of))},
            "question": q,
            "intent": intent,
            "mode": "fallback",
            "tools_called": [],
            "tools_notice": tools_notice(),
            "answer": body["answer"],
            "evidence": body.get("evidence"),
            "suggestions": body.get("suggestions") or [],
            "grounded": True,
            "empty_warehouse": False,
        }

    def _ask_with_tools(self, q: str, history: list[dict[str, str]]) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(tenant_id=self.tenant_id, as_of=self.as_of.isoformat())}
        ]
        for turn in history[-16:]:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": q})
        tools_called: list[str] = []
        evidence: dict[str, Any] = {}
        final = ""
        for _ in range(8):
            raw = complete_chat(messages, TOOL_SCHEMAS)
            choice = (raw.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )
                for call in tool_calls:
                    fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                    name = str(fn.get("name") or "")
                    args = parse_tool_args(fn.get("arguments"))
                    payload, _err = run_tool(
                        name,
                        args,
                        warehouse=self.warehouse,
                        as_of=self.as_of,
                        company=self.company,
                        alerts_fn=self.alerts,
                    )
                    tools_called.append(name)
                    evidence[f"{name}_{len(tools_called)}"] = payload
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "content": dump_tool_result(payload),
                        }
                    )
                continue
            final = (msg.get("content") or "").strip()
            break
        if not final:
            final = "The warehouse tools returned no prose. Ask again, or the data is not in the dump."
        return {
            "banner": PRODUCT_BANNER,
            "tenant_id": self.tenant_id,
            "as_of": self.as_of.isoformat(),
            "last_closed_month": {k: v.isoformat() for k, v in zip(("start", "end"), last_closed_month(self.as_of))},
            "question": q,
            "intent": "tool_chat",
            "mode": "tools",
            "model": xai_model(),
            "tools_called": tools_called,
            "tools_notice": None,
            "answer": final,
            "evidence": evidence,
            "suggestions": [],
            "grounded": True,
            "empty_warehouse": False,
        }

    def alerts(self, defs: list[AlertDef] | None = None) -> dict[str, Any]:
        hits = evaluate_alerts(self.warehouse, self.as_of, defs, company=self.company)
        return {
            "banner": PRODUCT_BANNER,
            "as_of": self.as_of.isoformat(),
            "alerts": [
                {
                    "id": h.id,
                    "name": h.name,
                    "triggered": h.triggered,
                    "message": h.message,
                    "evidence": h.evidence,
                }
                for h in hits
            ],
        }

    def scheduled(self) -> dict[str, Any]:
        return {
            "banner": PRODUCT_BANNER,
            "runs": run_scheduled(self.warehouse, self.as_of, company=self.company),
        }

    def snapshot(self) -> dict[str, Any]:
        return {"banner": PRODUCT_BANNER, "snapshot": snapshot(self.warehouse, self.as_of, company=self.company)}

    def _route(self, question: str) -> tuple[Callable[[str], dict[str, Any]], str]:
        q = question.lower()
        if re.search(r"payroll|profitab", q):
            return self._payroll, "therapist_profit"
        if re.search(r"caseload|fill a caseload|new clinician", q):
            return self._caseload, "caseload_fill"
        if re.search(r"\bar\b|past 30|collections sitting|aging", q):
            return self._ar, "ar_past_30"
        if re.search(r"cancel", q):
            return self._cancelation, "cancelation"
        if re.search(r"referral|another therapist|drop-?off", q):
            return self._referrals, "referrals"
        if re.search(r"churn", q):
            return self._churn, "churn"
        if re.search(r"days to pay", q):
            return self._days_to_pay, "days_to_pay"
        if re.search(r"avg paid|average paid", q):
            return self._avg_paid, "avg_paid"
        if re.search(r"avg collection|average collection", q):
            return self._avg_collections, "avg_collections"
        if re.search(r"improve|what can i do", q):
            return self._improve, "improve_business"
        return self._improve, "improve_business"

    def _cancelation(self, question: str) -> dict[str, Any]:
        result = cancelation_rate(self.warehouse, self.as_of, months=3, company=self.company)
        threshold = 0.25
        m = re.search(r"(\d+)\s*%", question)
        if m:
            threshold = int(m.group(1)) / 100
        rate = result.value
        if rate is None:
            return {"answer": result.unavailable, "evidence": result.to_dict()}
        over = rate > threshold
        d = result.details
        answer = (
            f"Cancelation is {_pct(rate)} over the last 3 closed months "
            f"({d['window_start']} through {d['window_end']}). "
            f"That is {'over' if over else 'not over'} {threshold:.0%}. "
            f"Formula: (Cancelled + No Show) / (Complete + Cancelled + No Show) = "
            f"({d['cancelled']} + {d['no_show']}) / "
            f"({d['complete']} + {d['cancelled']} + {d['no_show']}) = "
            f"{d['numerator']}/{d['denominator']}. "
            f"Pending ({d['pending_excluded']}) and Waiting ({d['waiting_excluded']}) are out."
        )
        suggestions = []
        if over:
            suggestions.append(
                f"Cancelation is {_pct(rate)}, above {threshold:.0%}. "
                "Focus on Cancelled + No Show volume in the closed-month window; "
                "Pending/Waiting are already excluded and are not the rate."
            )
        return {"answer": answer, "evidence": result.to_dict(), "suggestions": suggestions}

    def _ar(self, _question: str) -> dict[str, Any]:
        result = ar_past_30_days(self.warehouse, self.as_of, company=self.company)
        rows = result.value or []
        if not rows:
            return {
                "answer": result.unavailable or "No Completes older than 30 days with InsBalance > 0.",
                "evidence": result.to_dict(),
            }
        lines = [
            f"- {r['payer']} @ {r['location']}: ${r['ins_balance']:.2f} InsBalance "
            f"on {r['claims']} Completes, avg age {r['avg_age_days']:.0f} days"
            for r in rows[:12]
        ]
        answer = (
            "Dollar AR aged > 30 days is SUM(InsBalance) on Completes with InsBalance > 0, "
            "by PrimaryPayorName × LocationName. Insurance only. "
            "This is not billed − paid, not PatBalance, and not Tableau NET AR. "
            "Expected-recovery (InsPaid × open-claim count) is a separate question.\n"
            + "\n".join(lines)
        )
        suggestions = []
        top = rows[0]
        suggestions.append(
            f"{top['payer']} at {top['location']} has ${top['ins_balance']:.2f} InsBalance "
            f"on {top['claims']} Completes aged > 30 days. Work that payer/location pair first."
        )
        return {"answer": answer, "evidence": result.to_dict(), "suggestions": suggestions}

    def _referrals(self, question: str) -> dict[str, Any]:
        change = referral_volume_change(self.warehouse, self.as_of, company=self.company)
        refs = referrals(self.warehouse, self.as_of, months=1, company=self.company)
        cur = change.details.get("current_referrals", 0)
        prior = change.details.get("prior_referrals", 0)
        conv = refs.value.get("conversion") if refs.value else None
        drops = [
            s
            for s in change.details.get("by_source", [])
            if s.get("change") is not None and s["change"] <= -0.10
        ]
        drop_lines = [
            f"- {s['source']}: {s['prior']} → {s['current']} ({s['change']:.1%})"
            for s in drops
        ] or ["- No source declined 10% or more vs the prior closed month."]
        extra = ""
        suggestions = []
        if re.search(r"another therapist|another clinician|fte|staff", question.lower()):
            fc = forecast(self.warehouse, self.as_of, company=self.company)
            extra = "\nStaffing working model (clinic×discipline, last closed month Completes + refs/mo × 50% conversion):\n"
            for line in fc["by_discipline"]:
                extra += (
                    f"- {line['discipline']}: demand {line['demand_visits_next_month']:.1f} visits next month, "
                    f"rounded FTE {line['fte_rounded']}, current headcount {line['headcount']} "
                    f"(churn {line['churn_rate_used']:.1%} via {line['churn_source']}).\n"
                )
                if line["fte_rounded"] > line["headcount"]:
                    suggestions.append(
                        f"{line['discipline']} rounded FTE {line['fte_rounded']} exceeds headcount "
                        f"{line['headcount']}. Volume may support additional capacity at that discipline."
                    )
                else:
                    suggestions.append(
                        f"{line['discipline']} rounded FTE {line['fte_rounded']} does not exceed headcount "
                        f"{line['headcount']}. Closed-month volume does not, by itself, support another therapist."
                    )
        answer = (
            f"Referrals in the last closed month: {cur} "
            f"(prior closed month {prior}, change {_pct(change.value)}). "
            f"Conversion (Completed?=1 / referrals) last closed month: {_pct(conv)}. "
            "EVAL notes are not conversion.\n"
            "Referral-source drop-off (≥10% vs prior closed month):\n"
            + "\n".join(drop_lines)
            + extra
        )
        if drops:
            worst = min(drops, key=lambda s: s["change"] if s["change"] is not None else 0)
            suggestions.append(
                f"{worst['source']} dropped {worst['change']:.1%} "
                f"({worst['prior']} → {worst['current']}). That is the source to inspect first."
            )
        return {
            "answer": answer,
            "evidence": {"referrals": refs.to_dict(), "change": change.to_dict()},
            "suggestions": suggestions,
        }

    def _caseload(self, _question: str) -> dict[str, Any]:
        result = caseload_fill(self.warehouse, self.as_of, company=self.company)
        if result.unavailable:
            return {
                "answer": (
                    "The data is not there to say how long a new clinician takes to fill a caseload. "
                    + result.unavailable
                ),
                "evidence": result.to_dict(),
            }
        lines = [
            f"- {r['therapist']} ({r['discipline']}): {r['months_to_fill']} months to reach "
            f"{r['target_weekly']} Completes/week"
            for r in result.value
        ]
        months = [r["months_to_fill"] for r in result.value]
        avg = sum(months) / len(months)
        answer = (
            f"{len(months)} clinician(s) in this dump reached the staffing weekly-visit target "
            f"(OT/PT 35, ST 70 Completes/week). Average months to fill among those who reached it: "
            f"{avg:.1f}.\n" + "\n".join(lines)
        )
        if result.details.get("insufficient"):
            answer += (
                f"\n{len(result.details['insufficient'])} clinician(s) never reached the target "
                "in this dump and are not averaged in."
            )
        return {"answer": answer, "evidence": result.to_dict()}

    def _payroll(self, _question: str) -> dict[str, Any]:
        if payroll_present(self.warehouse):
            return {
                "answer": "Payroll is present but no profitability model is implemented in this first pass.",
                "evidence": {},
            }
        return {
            "answer": (
                "Payroll is not in this dump, so therapist profitability after payroll cannot be computed. "
                "No payroll number is invented."
            ),
            "evidence": {"payroll_present": False},
        }

    def _churn(self, _question: str) -> dict[str, Any]:
        result = churn(self.warehouse, self.as_of, company=self.company)
        d = result.details
        answer = (
            f"Clinic churn (Company × Discipline × PatientId) is {_pct(result.value)} "
            f"for prior {d['prior_month_start']}–{d['prior_month_end']} "
            f"vs current {d['current_month_start']}–{d['current_month_end']} "
            f"(closed months). Prior active {d['prior_active']}, churned {d['churned']}. "
            "Patients with first Complete DOS on/after the prior month start are dropped. "
            "PATIENT.PatientActive is not used."
        )
        return {"answer": answer, "evidence": result.to_dict()}

    def _days_to_pay(self, _question: str) -> dict[str, Any]:
        result = days_to_pay(self.warehouse, self.as_of, company=self.company)
        if result.unavailable:
            return {"answer": result.unavailable, "evidence": result.to_dict()}
        lines = [f"- {r['payer']}: {r['avg_days']:.1f} days (n={r['claims']})" for r in result.value]
        return {
            "answer": "Days to pay (Completes, InsPaid>0, non-negative DATEDIFF, min 20 claims):\n" + "\n".join(lines),
            "evidence": result.to_dict(),
        }

    def _avg_paid(self, _question: str) -> dict[str, Any]:
        result = avg_paid(self.warehouse, self.as_of, company=self.company)
        lines = [f"- {r['payer']}: ${r['avg_ins_paid']:.2f} (n={r['claims']}, InsPaid>0)" for r in (result.value or [])]
        return {
            "answer": result.unavailable or ("Avg Paid by payer (InsPaid>0, last 3 months through as_of):\n" + "\n".join(lines)),
            "evidence": result.to_dict(),
        }

    def _avg_collections(self, _question: str) -> dict[str, Any]:
        result = avg_collections(self.warehouse, self.as_of, company=self.company)
        lines = [
            f"- {r['payer']}: ${r['avg_ins_paid']:.2f} (n={r['claims']}, zeros/partials included)"
            for r in (result.value or [])
        ]
        return {
            "answer": result.unavailable
            or (
                "Avg Collections by payer (InsPaid, 60-day lag then 3 months back):\n" + "\n".join(lines)
            ),
            "evidence": result.to_dict(),
        }

    def _improve(self, _question: str) -> dict[str, Any]:
        cancel = cancelation_rate(self.warehouse, self.as_of, months=3, company=self.company)
        ar = ar_past_30_days(self.warehouse, self.as_of, company=self.company)
        refs = referral_volume_change(self.warehouse, self.as_of, company=self.company)
        ch = churn(self.warehouse, self.as_of, company=self.company)
        fc = forecast(self.warehouse, self.as_of, company=self.company)
        suggestions = []
        parts = [
            f"Closed-month snapshot as of {self.as_of.isoformat()}.",
            f"Cancelation (last 3 closed months): {_pct(cancel.value)} "
            f"({cancel.details.get('numerator')}/{cancel.details.get('denominator')}).",
            f"Churn (last two closed months): {_pct(ch.value)} "
            f"({ch.details.get('churned')} of {ch.details.get('prior_active')} prior-active).",
            f"Referrals last closed month {refs.details.get('current_referrals')} "
            f"vs prior {refs.details.get('prior_referrals')} ({_pct(refs.value)}).",
        ]
        if cancel.value is not None and cancel.value > 0.25:
            suggestions.append(
                f"Cancelation is {_pct(cancel.value)}, above 25%. "
                f"The movable volume is {cancel.details['cancelled']} Cancelled + "
                f"{cancel.details['no_show']} No Show in the closed-month window."
            )
        if ar.value:
            top = ar.value[0]
            suggestions.append(
                f"Work {top['payer']} at {top['location']} first: "
                f"${top['ins_balance']:.2f} InsBalance on {top['claims']} Completes aged > 30 days."
            )
            parts.append(
                f"Dollar AR aged > 30 days (SUM InsBalance): "
                f"${sum(r['ins_balance'] for r in ar.value):.2f} across "
                f"{len(ar.value)} payer×location pairs."
            )
        drops = [
            s
            for s in refs.details.get("by_source", [])
            if s.get("change") is not None and s["change"] <= -0.10
        ]
        if drops:
            worst = min(drops, key=lambda s: s["change"])
            suggestions.append(
                f"{worst['source']} referral volume dropped {worst['change']:.1%} "
                f"({worst['prior']} → {worst['current']})."
            )
        for line in fc["by_discipline"]:
            if line["fte_rounded"] > line["headcount"]:
                suggestions.append(
                    f"{line['discipline']}: staffing model wants {line['fte_rounded']} FTE vs "
                    f"headcount {line['headcount']} (demand {line['demand_visits_next_month']:.1f} visits)."
                )
        if not suggestions:
            suggestions.append(
                "No cancelation, AR, referral-drop, or FTE-gap trigger fired on this closed-month snapshot. "
                "No additional action is invented."
            )
        parts.append("Grounded actions from the data (no invented numbers):")
        parts.extend(f"- {s}" for s in suggestions)
        parts.append("Therapist profitability after payroll: payroll is not in this dump.")
        return {
            "answer": "\n".join(parts),
            "evidence": {
                "cancelation": cancel.to_dict(),
                "ar": ar.to_dict(),
                "referrals": refs.to_dict(),
                "churn": ch.to_dict(),
                "staffing": fc,
            },
            "suggestions": suggestions,
        }
