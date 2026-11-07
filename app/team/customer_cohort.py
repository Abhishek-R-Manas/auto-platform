"""Data-only aggregation of explicitly selected organization reports, without double counting."""
from datetime import datetime, timezone
import math
import re
from statistics import median

from ..research.catalog import aware, digest
from .customer import VERSION


def aggregate(reports):
    reports = list(reports)
    organizations = {}
    for report in reports:
        core = report["core"]
        if digest(core) != report["report_hash"] or core["schema_version"] != VERSION or not core["controlled_exercises_excluded"]:
            raise ValueError("Cohort input report failed integrity/scope checks")
        identifier = core["organization_id"]
        if not re.fullmatch(r"[a-f0-9]{32}",identifier):raise ValueError("Invalid organization identity")
        if identifier in organizations:raise ValueError("One organization may appear only once in a cohort")
        if core["commercial"] is None or "paying_organization" not in core:raise ValueError("Commercial cohort requires administrator reports")
        if any(type(core[key]) is not bool for key in ("paying_organization","renewed_organization","repeated_four_week_usage")):
            raise ValueError("Organization outcomes must be booleans, not inflated counters")
        if any(type(core[key]) is not int or not 0 <= core[key] <= 10000 for key in ("measured_reviews","completed_journeys","customer_engagements")):
            raise ValueError("Invalid organization sample counts")
        if core["completed_journeys"] > core["measured_reviews"]:
            raise ValueError("Completed journeys require distinct measured review cycles")
        reduction = core["review_time_reduction_pct"]
        if reduction is not None and (type(reduction) not in {int,float} or not math.isfinite(reduction) or reduction > 100):
            raise ValueError("Invalid review time comparison")
        commercial = core["commercial"]
        if any(type(commercial[key]) is not int or commercial[key] < 0 for key in ("payments_paise","refunds_paise","delivery_costs_paise","fully_paid_invoices","paid_renewals")):
            raise ValueError("Commercial report requires nonnegative integer paise and counts")
        if commercial["refunds_paise"] > commercial["payments_paise"] or commercial["net_receipts_paise"] != commercial["payments_paise"]-commercial["refunds_paise"]:
            raise ValueError("Commercial receipts do not reconcile")
        if core["paying_organization"] != bool(commercial["fully_paid_invoices"]) or core["renewed_organization"] != bool(commercial["paid_renewals"]):
            raise ValueError("Organization outcomes contradict commercial evidence")
        if commercial["paid_renewals"] > commercial["fully_paid_invoices"]:
            raise ValueError("Paid renewals cannot exceed fully paid invoices")
        days = core["observation_span_days"]
        weeks = core["active_iso_weeks"]
        if type(days) not in {int,float} or not math.isfinite(days) or days < 0 or type(weeks) is not int or weeks < 0:
            raise ValueError("Invalid elapsed observation evidence")
        if core["repeated_four_week_usage"] != (core["completed_journeys"] >= 2 and days >= 28 and weeks >= 4):
            raise ValueError("Four-week outcome contradicts recorded elapsed activity")
        if type(commercial["net_receipts_paise"]) is not int or type(commercial["net_cash_after_recorded_costs_paise"]) is not int or commercial["net_cash_after_recorded_costs_paise"] != commercial["net_receipts_paise"]-commercial["delivery_costs_paise"]:
            raise ValueError("Recorded commercial cash does not reconcile")
        if aware(core["exported_at"]) > datetime.now(timezone.utc):raise ValueError("Future-dated cohort report")
        if core["mvp5_accepted"] or core["source_authenticity_verified"]:raise ValueError("Self-reported operation cannot assert independent acceptance")
        organizations[identifier] = core
    rows = list(organizations.values())
    paying = sum(r["paying_organization"] for r in rows)
    renewed = sum(r["renewed_organization"] for r in rows)
    repeated = sum(r["repeated_four_week_usage"] for r in rows)
    reductions = [r["review_time_reduction_pct"] for r in rows if r["measured_reviews"] >= 2 and r["review_time_reduction_pct"] is not None]
    integrations = {}
    for row in rows:
        for key in set(row["integration_demand"]):integrations[key] = integrations.get(key,0)+1
    core = dict(schema_version="mvp5/cohort/1.0",created_at=datetime.now(timezone.utc).isoformat(),organizations=len(rows),
        input_reports={identifier:reports_hash for identifier,reports_hash in ((r["core"]["organization_id"],r["report_hash"]) for r in reports)},
        paying_organizations=paying,renewed_organizations=renewed,repeated_four_week_organizations=repeated,
        time_comparison_organizations=len(reductions),median_review_time_reduction_pct=median(reductions) if reductions else None,
        net_receipts_paise=sum(r["commercial"]["net_receipts_paise"] for r in rows),
        recorded_delivery_costs_paise=sum(r["commercial"]["delivery_costs_paise"] for r in rows),
        integration_demand_organizations=dict(sorted(integrations.items(),key=lambda pair:(-pair[1],pair[0]))),
        target_checks=dict(five_paying_organizations=paying >= 5,three_renewed_organizations=renewed >= 3,
            four_organizations_with_repeated_four_week_usage=repeated >= 4,
            median_review_reduction_50_percent=len(reductions) >= 4 and median(reductions) >= 50),
        evidence_assurance="SELF_REPORTED_CUSTOMER_OPERATION",source_authenticity_verified=False,mvp5_accepted=False,
        note="Counts describe selected organization reports. Hashes do not verify customers, invoices, payments, source rights or financial acceptance.")
    return dict(core=core,report_hash=digest(core))
