"""MVP-5 tenant/privacy boundaries, economic records and elapsed workflow evidence."""
import copy
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.team.api import create_team_app
from app.team.customer import check_customer_database
from app.team.customer_cohort import aggregate
from app.team.recovery import snapshot, restore
from app.team.service import Team
from app.research.catalog import digest
from app.research.fixtures import contract_fixture
from app.research.jobs import execute
from test_team import team, evidence, PASSWORD, EMAIL, HEADERS


def engage(service, author, kind="CUSTOMER_REPORTED", key="engagement-one"):
    service.workspace
    return service.customers.create_engagement(author,dict(request_id=key,name="Controlled organization workflow",
        evidence_kind=kind,workflow_reference="Controlled workflow artifact",baseline_review_seconds=600,
        baseline_reference="Controlled baseline measurement"))


def cycle(service, author, future=False, kind="CUSTOMER_REPORTED"):
    engagement=engage(service,author,kind)
    if future:
        definition,body=contract_fixture()
        definition["rules"]["entry_at"]="2026-11-09T09:30:00+05:30"
        definition["rules"]["exit_at"]="2026-11-09T15:00:00+05:30"
        dataset=service.import_data(author,body)
        saved=service.save(author,dict(request_id="future-definition",definition=definition,dataset_id=dataset["id"]))
        p=service.freeze(author,dict(request_id="future-protocol",contract_id=saved["id"],dataset_id=dataset["id"],
            observation_cutoff="2026-11-09T15:00:10+05:30",allow_unverified_data=True,max_trials=6))
        attempt=service.start(author,dict(request_id="future-attempt",protocol_id=p["id"],kind="BASELINE"))
        execute(service.workspace.research.root,attempt["run_id"])
        packet=service.capture(author,dict(request_id="future-capture",protocol_id=p["id"]))
    else:
        p,packet=evidence(service,author)
    item=service.customers.create_cycle(author,dict(request_id="cycle-create",engagement_id=engagement["id"],packet_id=packet["id"],question="Controlled workflow question"))
    return engagement,item,p,packet


def measured(service, author, reviewer, future=False, kind="CUSTOMER_REPORTED"):
    service.identity.clock=lambda:datetime(2026,11,7,10,tzinfo=timezone.utc).timestamp()
    engagement,item,p,packet=cycle(service,author,future,kind)
    ops=service.customers
    started=ops.timer(reviewer,dict(request_id="timer-start",cycle_id=item["id"],action="START"))
    service.identity.clock=lambda:datetime(2026,11,7,10,0,10,tzinfo=timezone.utc).timestamp()
    review=service.review(reviewer,dict(request_id="measured-review",packet_id=packet["id"],decision="ACCEPT",comment="Controlled snapshot review; source remains unverified"))
    finished=ops.timer(reviewer,dict(request_id="timer-finish",cycle_id=item["id"],action="FINISH",review_id=review["id"]))
    return engagement,item,p,packet,review,finished


def planned(service,author,reviewer,kind="CUSTOMER_REPORTED"):
    engagement,item,p,packet,review,finished=measured(service,author,reviewer,True,kind)
    plan=service.customers.plan(author,dict(request_id="prospective-plan",cycle_id=item["id"],review_id=review["id"],paper_book_reference="Controlled external paper book evidence"))
    return engagement,item,plan,review


def finance(service,author,engagement,kind="INVOICE",key="invoice-reference",**changes):
    body=dict(request_id=key,engagement_id=engagement["id"],kind=kind,amount_paise=10000,
        reference=key,evidence_reference="Controlled commercial evidence",occurred_at=service.customers.now().isoformat())
    if kind=="INVOICE":body.update(period_start="2026-11-01T00:00:00+00:00",period_end="2026-12-01T00:00:00+00:00")
    body.update(changes)
    return service.customers.finance(author,body)


def test_customer_journey_records_exact_packet_and_measured_wall_time(team):
    service,author,reviewer=team
    engagement,item,p,packet,review,finished=measured(service,author,reviewer)
    assert finished["core"]["elapsed_seconds"]==10
    assert item["core"]["bindings"]["packet_hash"]==service.packet(packet["id"])["core"]["packet"]["packet_hash"]
    report=service.customers.report(author)
    assert report["core"]["measured_reviews"]==1 and report["core"]["artha_median_elapsed_seconds"]==10
    assert report["core"]["review_time_reduction_pct"]==pytest.approx(98.333333333)
    assert not report["core"]["mvp5_accepted"]
    assert digest(report["core"])==report["report_hash"]


def test_request_retries_are_idempotent_after_state_changes(team):
    service,author,reviewer=team
    e,item,p,packet,review,finished=measured(service,author,reviewer)
    again=service.customers.timer(reviewer,dict(request_id="timer-finish",cycle_id=item["id"],action="FINISH",review_id=review["id"]))
    assert again==finished
    assert engage(service,author)==e
    with pytest.raises(ValueError,match="different"):
        service.customers.create_engagement(author,dict(request_id="engagement-one",name="Different inputs",evidence_kind="CUSTOMER_REPORTED",workflow_reference="ref",baseline_review_seconds=1,baseline_reference="ref"))


def test_self_review_timer_and_duplicate_packet_fail(team):
    service,author,reviewer=team
    e,item,p,packet=cycle(service,author)
    with pytest.raises(HTTPException,match="independent"):
        service.customers.timer(author,dict(request_id="self-timer",cycle_id=item["id"],action="START"))
    with pytest.raises(ValueError,match="already belongs"):
        service.customers.create_cycle(author,dict(request_id="duplicate-cycle",engagement_id=e["id"],packet_id=packet["id"],question="Same packet again"))


def test_commercial_money_is_paise_and_partial_payments_refunds_reconcile(team):
    service,author,reviewer=team;e=engage(service,author)
    invoice=finance(service,author,e)
    finance(service,author,e,"PAYMENT","partial-payment",amount_paise=6000,invoice_id=invoice["id"])
    assert not service.customers.report(author)["core"]["paying_organization"]
    finance(service,author,e,"PAYMENT","final-payment",amount_paise=4000,invoice_id=invoice["id"])
    finance(service,author,e,"COST","delivery-cost",amount_paise=2000,cost_category="COMPUTE")
    report=service.customers.report(author)["core"]
    assert report["paying_organization"] and report["commercial"]["net_cash_after_recorded_costs_paise"]==8000
    finance(service,author,e,"REFUND","partial-refund",amount_paise=1000,invoice_id=invoice["id"])
    report=service.customers.report(author)["core"]
    assert not report["paying_organization"] and report["commercial"]["net_receipts_paise"]==9000
    with pytest.raises(ValueError,match="outstanding"):
        finance(service,author,e,"PAYMENT","overpayment",amount_paise=2000,invoice_id=invoice["id"])
    with pytest.raises(ValueError,match="Refund exceeds"):
        finance(service,author,e,"REFUND","over-refund",amount_paise=10000,invoice_id=invoice["id"])


def test_finance_is_admin_only_and_redacted_from_other_roles(team):
    service,author,reviewer=team;e=engage(service,author)
    invoice=finance(service,author,e)
    with pytest.raises(HTTPException):finance(service,reviewer,e,"PAYMENT","forbidden-money",invoice_id=invoice["id"])
    with pytest.raises(HTTPException):engage(service,reviewer,key="forbidden-engagement")
    result=service.customers.dashboard(reviewer)
    assert not any(r["core"]["kind"]=="INVOICE" for r in result["records"])
    assert result["report"]["core"]["commercial"] is None
    assert "paying_organization" not in result["report"]["core"]


def test_paid_renewal_requires_both_invoice_periods_paid(team):
    service,author,reviewer=team;e=engage(service,author)
    invoice=finance(service,author,e)
    renewed=finance(service,author,e,key="renewal-invoice",renews_invoice_id=invoice["id"],period_start="2026-12-01T00:00:00+00:00",period_end="2027-01-01T00:00:00+00:00")
    finance(service,author,e,"PAYMENT","renewal-paid",invoice_id=renewed["id"])
    assert not service.customers.report(author)["core"]["renewed_organization"]
    finance(service,author,e,"PAYMENT","first-paid",invoice_id=invoice["id"])
    assert service.customers.report(author)["core"]["renewed_organization"]


def test_controlled_exercises_never_become_customer_metrics(team):
    service,author,reviewer=team
    e,item,p,packet,review,finished=measured(service,author,reviewer,kind="CONTROLLED_EXERCISE")
    invoice=finance(service,author,e)
    finance(service,author,e,"PAYMENT","exercise-paid",invoice_id=invoice["id"])
    report=service.customers.report(author)["core"]
    assert report["customer_engagements"]==0 and report["measured_reviews"]==0
    assert not report["paying_organization"] and report["commercial"]["net_receipts_paise"]==0


def test_prospective_plan_precommits_future_rules_without_execution_authority(team):
    service,author,reviewer=team
    e,item,plan,review=planned(service,author,reviewer)
    assert plan["core"]["authority"]=="OBSERVATION_JOURNAL_ONLY"
    assert not plan["core"]["engine_execution_enabled"]
    assert service.customers.report(author)["core"]["completed_journeys"]==0
    with pytest.raises(ValueError,match="after the planned start"):
        service.customers.observe(author,dict(request_id="too-early-note",plan_id=plan["id"],observed_at=service.customers.now().isoformat(),kind="ON_TRACK",note="Too early",evidence_reference="Controlled ref"))


def test_observation_elapsed_dates_end_and_independent_corrections(team):
    service,author,reviewer=team;e,item,plan,review=planned(service,author,reviewer)
    service.identity.clock=lambda:datetime(2026,11,9,4,1,tzinfo=timezone.utc).timestamp()
    obs=service.customers.observe(author,dict(request_id="gap-observation",plan_id=plan["id"],observed_at=service.customers.now().isoformat(),kind="DATA_GAP",note="Controlled feed gap",evidence_reference="Controlled recording"))
    with pytest.raises(HTTPException,match="another reviewer"):
        service.customers.correction(author,dict(request_id="self-correction",observation_id=obs["id"],decision="CONFIRMED",note="Self claim",evidence_reference="ref"))
    service.customers.correction(reviewer,dict(request_id="confirmed-correction",observation_id=obs["id"],decision="CONFIRMED",note="Observed gap checked",evidence_reference="ref"))
    with pytest.raises(ValueError,match="not actually elapsed"):
        service.customers.observe(author,dict(request_id="early-ending",plan_id=plan["id"],observed_at=service.customers.now().isoformat(),kind="ENDED",note="Too early",evidence_reference="ref"))
    service.identity.clock=lambda:datetime(2026,11,9,9,30,11,tzinfo=timezone.utc).timestamp()
    body=dict(request_id="completed-ending",plan_id=plan["id"],observed_at=service.customers.now().isoformat(),kind="ENDED",note="Recorded paper book outcome",evidence_reference="ref",reported_pnl_paise=1234)
    ended=service.customers.observe(author,body)
    assert service.customers.observe(author,body)==ended
    report=service.customers.report(author)["core"]
    assert report["completed_journeys"]==1 and not report["repeated_four_week_usage"]
    assert not ended["core"]["source_authenticity_verified"]


def test_material_changes_end_frozen_observation_plan(team):
    service,author,reviewer=team;e,item,plan,review=planned(service,author,reviewer)
    service.identity.clock=lambda:datetime(2026,11,9,4,1,tzinfo=timezone.utc).timestamp()
    body=dict(request_id="material-change",plan_id=plan["id"],observed_at=service.customers.now().isoformat(),kind="MATERIAL_CHANGE",note="Strategy changed; new cycle required",evidence_reference="ref")
    service.customers.observe(author,body)
    with pytest.raises(ValueError,match="has ended"):
        service.customers.observe(author,dict(body,request_id="new-observation",kind="ON_TRACK"))


@pytest.mark.parametrize("change",["superseded","reviewer_changed"])
def test_superseded_review_and_changed_reviewer_stop_observation(team,change):
    service,author,reviewer=team;e,item,plan,review=planned(service,author,reviewer)
    if change == "superseded":
        service.review(reviewer,dict(request_id="superseded-accept",packet_id=service.customers._get(item["id"])["core"]["packet_id"],decision="REQUEST_CHANGES",comment="More evidence required"))
    else:
        service.identity.change_member(author,reviewer["id"],["RESEARCHER"],True)
    service.identity.clock=lambda:datetime(2026,11,9,4,1,tzinfo=timezone.utc).timestamp()
    with pytest.raises(ValueError,match="latest independent|authority has changed"):
        service.customers.observe(author,dict(request_id="blocked-observation",plan_id=plan["id"],observed_at=service.customers.now().isoformat(),kind="ON_TRACK",note="Cannot continue",evidence_reference="ref"))


def test_cross_organization_records_fail_without_affecting_owner(team,tmp_path):
    service,author,reviewer=team;e=engage(service,author)
    other=Team(tmp_path/"other-customer")
    try:
        member=other.identity.bootstrap((other.root/"bootstrap.key").read_text(),"Other","other@example.test",PASSWORD)
        other.workspace
        with pytest.raises(HTTPException):
            other.customers.effort(member,dict(request_id="cross-org-effort",engagement_id=e["id"],kind="SUPPORT",minutes=10,evidence_reference="ref"))
        assert other.customers.report(member)["core"]["customer_engagements"]==0
    finally:other.close()


def test_backup_restore_keeps_customer_evidence_and_invalidates_old_plan(team,tmp_path):
    service,author,reviewer=team;e,item,plan,review=planned(service,author,reviewer)
    saved=snapshot(service)
    target=tmp_path/"restored-customer"
    restore(saved["path"],target)
    restored=Team(target,clock=lambda:datetime(2026,11,9,4,1,tzinfo=timezone.utc).timestamp())
    try:
        restored.workspace
        actor=restored.identity.user(author["id"])
        assert restored.customers._get(plan["id"])==plan
        with pytest.raises(ValueError,match="Recovered observation plans"):
            restored.customers.observe(actor,dict(request_id="restored-note",plan_id=plan["id"],observed_at=restored.customers.now().isoformat(),kind="ON_TRACK",note="Must renew",evidence_reference="ref"))
    finally:restored.close()


def test_customer_tampering_fails_closed(team):
    service,author,reviewer=team;e=engage(service,author)
    assert check_customer_database(service.workspace.db)["records_verified"]==1
    with service.workspace.db:
        service.workspace.db.execute("UPDATE team_customer_records SET document=replace(document,'Controlled organization workflow','Altered')")
    with pytest.raises(ValueError,match="integrity"):
        service.customers.report(author)


def test_cohort_counts_each_organization_once_and_rejects_tampering(team):
    service,author,reviewer=team;e=engage(service,author)
    invoice=finance(service,author,e)
    finance(service,author,e,"PAYMENT","cohort-payment",invoice_id=invoice["id"])
    report=service.customers.report(author)
    summary=aggregate([report])
    assert summary["core"]["paying_organizations"]==1 and not summary["core"]["mvp5_accepted"]
    with pytest.raises(ValueError,match="only once"):aggregate([report,report])
    altered=copy.deepcopy(report);altered["core"]["paying_organization"]=False
    with pytest.raises(ValueError,match="integrity"):aggregate([altered])
    with pytest.raises(ValueError,match="administrator"):aggregate([service.customers.report(reviewer)])


def test_customer_api_authentication_redaction_and_report_download(tmp_path):
    application=create_team_app(tmp_path/"api-team")
    with TestClient(application,base_url="http://127.0.0.1") as client:
        assert client.get("/api/team/customer/dashboard").status_code==401
        assert client.get("/api/team/customer/report").status_code==401
        key=(application.state.team.root/"bootstrap.key").read_text()
        assert client.post("/api/team/bootstrap",headers=HEADERS,json=dict(bootstrap_key=key,organization_name="Controlled",email=EMAIL,password=PASSWORD)).status_code==200
        login=client.post("/api/team/login",headers=HEADERS,json=dict(email=EMAIL,password=PASSWORD)).json()
        headers=dict(HEADERS,**{"X-Artha-CSRF":login["csrf"]})
        bad=client.post("/api/team/customer/engagements",headers=headers,json=dict(name="private sentinel"))
        assert bad.status_code==422 and "private sentinel" not in bad.text
        downloaded=client.get("/api/team/customer/report")
        report=json.loads(downloaded.content)
        assert digest(report["core"])==report["report_hash"]
        assert not report["core"]["mvp5_accepted"] and report["core"]["customer_engagements"]==0


def test_future_finance_duplicate_reference_and_void_boundaries(team):
    service,author,reviewer=team;e=engage(service,author)
    invoice=finance(service,author,e)
    finance(service,author,e,"PAYMENT","one-bank-reference",invoice_id=invoice["id"])
    with pytest.raises(ValueError,match="already recorded"):
        finance(service,author,e,"PAYMENT","different-request",reference="one-bank-reference",invoice_id=invoice["id"],amount_paise=1)
    with pytest.raises(ValueError,match="Refund receipts"):
        finance(service,author,e,"VOID_INVOICE","void-paid",invoice_id=invoice["id"],amount_paise=None)
    with pytest.raises(ValueError,match="future"):
        finance(service,author,e,"COST","future-cost",cost_category="DATA",occurred_at=(service.customers.now()+timedelta(days=1)).isoformat())
    finance(service,author,e,"REFUND","full-refund",invoice_id=invoice["id"])
    finance(service,author,e,"VOID_INVOICE","void-refunded",invoice_id=invoice["id"],amount_paise=None)
    with pytest.raises(ValueError,match="voided"):
        finance(service,author,e,"PAYMENT","cannot-pay-void",invoice_id=invoice["id"])


def test_cross_engagement_invoice_and_fractional_paise_are_rejected(team):
    service,author,reviewer=team;e=engage(service,author)
    invoice=finance(service,author,e)
    other=engage(service,author,key="second-engagement")
    with pytest.raises(ValueError,match="another engagement"):
        finance(service,author,other,"PAYMENT","wrong-engagement-payment",invoice_id=invoice["id"])
    with pytest.raises(ValueError):
        finance(service,author,e,"COST","fractional-paise",amount_paise=1.5,cost_category="DATA")


def test_late_recording_cannot_manufacture_four_week_activity(team):
    service,author,reviewer=team;e,item,plan,review=planned(service,author,reviewer)
    service.identity.clock=lambda:datetime(2026,12,8,10,tzinfo=timezone.utc).timestamp()
    ended=service.customers.observe(author,dict(request_id="late-ending",plan_id=plan["id"],observed_at=plan["core"]["end_at"],kind="ENDED",note="Late supplied closing evidence",evidence_reference="ref"))
    assert ended["core"]["timeliness"]=="LATE_RECORDED"
    report=service.customers.report(author)["core"]
    assert report["observation_span_days"]==0 and report["active_iso_weeks"]==0
    assert not report["repeated_four_week_usage"]


def test_cohort_rejects_rehashed_counter_inflation(team):
    service,author,reviewer=team;engage(service,author)
    report=service.customers.report(author)
    report["core"]["paying_organization"]=5
    report["report_hash"]=digest(report["core"])
    with pytest.raises(ValueError,match="booleans"):
        aggregate([report])


def test_zero_elapsed_review_cannot_be_claimed_as_measured_saving(team):
    service,author,reviewer=team
    service.identity.clock=lambda:datetime(2026,11,7,10,tzinfo=timezone.utc).timestamp()
    e,item,p,packet=cycle(service,author)
    service.customers.timer(reviewer,dict(request_id="zero-timer-start",cycle_id=item["id"],action="START"))
    review=service.review(reviewer,dict(request_id="zero-time-review",packet_id=packet["id"],decision="ACCEPT",comment="Controlled snapshot"))
    with pytest.raises(ValueError,match="positive elapsed"):
        service.customers.timer(reviewer,dict(request_id="zero-timer-finish",cycle_id=item["id"],action="FINISH",review_id=review["id"]))


def test_effort_and_repeated_integration_demand_are_counted_without_duplicate_organizations(team):
    service,author,reviewer=team;e=engage(service,author)
    service.customers.effort(reviewer,dict(request_id="actual-effort",engagement_id=e["id"],kind="SUPPORT",minutes=12,evidence_reference="controlled timesheet"))
    for n in range(2):
        service.customers.demand(author,dict(request_id="integration-request-"+str(n),engagement_id=e["id"],integration="vendor-import",problem="Repeated controlled import friction",evidence_reference="controlled evidence"))
    report=service.customers.report(author)
    assert report["core"]["effort_minutes"]["SUPPORT"]==12
    assert aggregate([report])["core"]["integration_demand_organizations"]=={"vendor-import":1}


def test_repeat_review_rounds_accumulate_instead_of_inflating_savings(team):
    service,author,reviewer=team
    e,item,p,packet,review,finished=measured(service,author,reviewer)
    service.identity.clock=lambda:datetime(2026,11,7,10,0,20,tzinfo=timezone.utc).timestamp()
    service.customers.timer(reviewer,dict(request_id="second-round-start",cycle_id=item["id"],action="START"))
    new_review=service.review(reviewer,dict(request_id="second-round-review",packet_id=packet["id"],decision="ACCEPT",comment="Controlled additional review round"))
    service.identity.clock=lambda:datetime(2026,11,7,10,0,40,tzinfo=timezone.utc).timestamp()
    service.customers.timer(reviewer,dict(request_id="second-round-finish",cycle_id=item["id"],action="FINISH",review_id=new_review["id"]))
    report=service.customers.report(author)["core"]
    assert report["measured_reviews"]==1 and report["measured_review_rounds"]==2
    assert report["artha_median_elapsed_seconds"]==30 and report["review_time_reduction_pct"]==95


def test_actual_clock_offline_cohort_cli_excludes_controlled_receipts(team,tmp_path):
    service,author,reviewer=team;e=engage(service,author,kind="CONTROLLED_EXERCISE")
    invoice=finance(service,author,e)
    finance(service,author,e,"PAYMENT","controlled-cli-payment",invoice_id=invoice["id"])
    path=tmp_path/"controlled-report.json";path.write_text(json.dumps(service.customers.report(author)))
    result=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve().parents[1]/"tools/customer_cohort.py"),str(path)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    summary=json.loads(result.stdout)["core"]
    assert summary["organizations"]==1 and summary["paying_organizations"]==0 and summary["net_receipts_paise"]==0


def test_whitespace_cannot_satisfy_customer_evidence_reference(team):
    service,author,reviewer=team;service.workspace
    with pytest.raises(ValueError):
        service.customers.create_engagement(author,dict(request_id="empty-evidence",name="Pilot",evidence_kind="CUSTOMER_REPORTED",
            workflow_reference="   ",baseline_review_seconds=600,baseline_reference="review artifact"))


def test_entry_corrections_are_audited_and_do_not_invent_refunds(team):
    service,author,reviewer=team;e=engage(service,author)
    invoice=finance(service,author,e)
    payment=finance(service,author,e,"PAYMENT","mistyped-receipt",invoice_id=invoice["id"])
    cost=finance(service,author,e,"COST","mistyped-cost",amount_paise=2000,cost_category="DATA")
    finance(service,author,e,"REVERSE_ENTRY","correct-receipt",amount_paise=None,target_record_id=payment["id"])
    finance(service,author,e,"REVERSE_ENTRY","correct-cost",amount_paise=None,target_record_id=cost["id"])
    report=service.customers.report(author)["core"]
    assert report["commercial"]["net_receipts_paise"]==0 and report["commercial"]["refunds_paise"]==0
    assert report["commercial"]["delivery_costs_paise"]==0 and not report["paying_organization"]
    assert service.customers._get(payment["id"])==payment
    with pytest.raises(ValueError,match="already corrected"):
        finance(service,author,e,"REVERSE_ENTRY","repeat-correction",amount_paise=None,target_record_id=payment["id"])
    assert all(r["core"]["kind"]!="REVERSE_ENTRY" for r in service.customers.dashboard(reviewer)["records"])


def test_prospective_planning_rejects_exposed_future_seed_observations(team):
    service,author,reviewer=team
    service.identity.clock=lambda:datetime(2026,11,6,3,30,tzinfo=timezone.utc).timestamp()
    e,item,p,packet=cycle(service,author,future=True)
    service.customers.timer(reviewer,dict(request_id="future-seed-timer",cycle_id=item["id"],action="START"))
    service.identity.clock=lambda:datetime(2026,11,6,3,30,10,tzinfo=timezone.utc).timestamp()
    review=service.review(reviewer,dict(request_id="future-seed-review",packet_id=packet["id"],decision="ACCEPT",comment="Controlled future input rejection"))
    service.customers.timer(reviewer,dict(request_id="future-seed-finish",cycle_id=item["id"],action="FINISH",review_id=review["id"]))
    with pytest.raises(ValueError,match="Future observations"):
        service.customers.plan(author,dict(request_id="future-seed-plan",cycle_id=item["id"],review_id=review["id"],paper_book_reference="Controlled paper book"))


def test_changed_build_keeps_prior_plan_historical(team,monkeypatch):
    import app.team.customer as module
    service,author,reviewer=team;e,item,plan,review=planned(service,author,reviewer)
    service.identity.clock=lambda:datetime(2026,11,9,4,1,tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(module,"CURRENT_BUILD",dict(module.CURRENT_BUILD,code_hash="f"*64))
    with pytest.raises(ValueError,match="earlier build"):
        service.customers.observe(author,dict(request_id="changed-build-note",plan_id=plan["id"],observed_at=service.customers.now().isoformat(),kind="ON_TRACK",note="Cannot relabel old plan",evidence_reference="ref"))
