"""MVP-3 authentic identities, independent reviews, organization boundaries and active revocation."""
from datetime import datetime,timezone,timedelta
import copy,json,time
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.team.api import create_team_app
from app.team.auth import check_auth_database
from app.team.service import Team,check_review_database
from app.team.gates import authority_reason
from app.research.jobs import execute
from app.contract_research import verify_packet

PASSWORD="Controlled team password 123456"
HEADERS={"X-Artha-Client":"paper-workspace"}
EMAIL="author@example.test"

@pytest.fixture
def team(tmp_path):
    value=Team(tmp_path/"team")
    key=(value.root/"bootstrap.key").read_text()
    author=value.identity.bootstrap(key,"Controlled organization",EMAIL,PASSWORD)
    value.workspace.research.schedule=lambda identifier:None
    invite=value.identity.invite(author,"reviewer@example.test","Independent reviewer",["REVIEWER"])
    reviewer=value.identity.accept(invite["invitation_token"],PASSWORD)
    yield value,author,reviewer
    value.close()

def prepare(team,author):
    sample=team.example(author,"controlled-example")
    return team.freeze(author,dict(request_id="controlled-protocol",contract_id=sample["contract"]["id"],dataset_id=sample["dataset_id"],
        observation_cutoff="2026-11-06T15:00:10+05:30",allow_unverified_data=True,max_trials=8))

def run(team,actor,p,kind="BASELINE",key="baseline-attempt",grant=None):
    item=team.start(actor,dict(request_id=key,protocol_id=p["id"],kind=kind,grant_id=grant))
    execute(team.workspace.research.root,item["run_id"])
    return team.experiments.trial(item["id"])

def evidence(team,author):
    p=prepare(team,author)
    run(team,author,p)
    run(team,author,p,"REPRODUCE","reproduce-attempt")
    packet=team.capture(author,dict(request_id="controlled-capture",protocol_id=p["id"]))
    return p,packet

def scope(team,reviewer,p,packet,limit=2,scenarios=None):
    review=team.review(reviewer,dict(request_id="review-snapshot",packet_id=packet["id"],decision="ACCEPT",comment="Controlled accounting and reproducibility reviewed; source remains synthetic."))
    return team.grant(reviewer,dict(request_id="scope-approved",packet_id=packet["id"],review_id=review["id"],
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),max_new_trials=limit,
        scenarios=scenarios or ["BASELINE","REPRODUCE","COST_STRESS"],acknowledge_unqualified_research=True))

def test_authentication_csrf_logout_and_secure_cookie(tmp_path):
    app=create_team_app(tmp_path/"team")
    with TestClient(app,base_url="https://localhost") as client:
        assert client.get("/api/team/dashboard").status_code==401
        key=(app.state.team.root/"bootstrap.key").read_text()
        body=dict(bootstrap_key=key,organization_name="Controlled team",email=EMAIL,password=PASSWORD)
        assert client.post("/api/team/bootstrap",json=body).status_code==403
        assert client.post("/api/team/bootstrap",json=body,headers=HEADERS).status_code==200
        assert not (app.state.team.root/"bootstrap.key").exists()
        signed=client.post("/api/team/login",json=dict(email=EMAIL,password=PASSWORD),headers=HEADERS)
        assert signed.status_code==200,signed.text
        cookie=signed.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie and "Path=/api/team" in cookie
        assert "password" not in signed.text and "token_hash" not in signed.text
        csrf=signed.json()["csrf"];headers=dict(HEADERS,**{"X-Artha-CSRF":csrf})
        assert client.post("/api/team/example",json=dict(request_id="csrf-missing"),headers=HEADERS).status_code==403
        assert client.post("/api/team/logout",json={},headers=headers).status_code==200
        assert client.get("/api/team/dashboard").status_code==401

def test_passwords_are_hashed_and_invitation_is_single_use(team):
    service,author,reviewer=team
    rows="".join(r[0] for r in service.identity.db.execute("SELECT document FROM team_identity_users"))
    assert PASSWORD not in rows and '"hash"' in rows and '"salt"' in rows
    invite=service.identity.invite(author,"new@example.test","New",["RESEARCHER"])
    assert not invite["user"]["active"]
    service.identity.accept(invite["invitation_token"],PASSWORD)
    with pytest.raises(HTTPException,match="Invitation"):service.identity.accept(invite["invitation_token"],PASSWORD)
    assert PASSWORD not in "".join(r[0] for r in service.identity.db.execute("SELECT document FROM team_identity_events"))

def test_role_matrix_and_last_admin_protection(team):
    service,author,reviewer=team
    for operation in (lambda:service.example(reviewer,"forbidden-example"),lambda:service.identity.members(reviewer),lambda:service.identity.invite(reviewer,"x@example.test","x",["ADMIN"])):
        with pytest.raises(HTTPException) as error:operation()
        assert error.value.status_code==403
    with pytest.raises(ValueError,match="at least one"):service.identity.change_member(author,author["id"],["RESEARCHER"],True)

def test_organization_authorship_ignores_untrusted_context(team):
    service,author,reviewer=team
    p=prepare(service,author)
    assert p["core"]["organization_ref"]==service.identity.organization()["id"]
    assert p["core"]["authenticated_context"]["actor_id"]==author["id"]
    assert p["core"]["identity_assurance"]=="AUTHENTICATED_ORGANIZATION"
    assert service.dashboard(author)["contracts"][0]["core"]["authenticated_context"]["actor_id"]==author["id"]

def test_snapshot_acceptance_is_independent_and_does_not_grant_execution(team):
    service,author,reviewer=team;p,packet=evidence(service,author)
    with pytest.raises(HTTPException,match="independent"):
        service.review(author,dict(request_id="self-review-attempt",packet_id=packet["id"],decision="ACCEPT",comment="Cannot self-approve"))
    service.review(reviewer,dict(request_id="independent-review",packet_id=packet["id"],decision="ACCEPT",comment="Reviewed"))
    with pytest.raises(HTTPException):
        run(service,author,p,key="unapproved-extra")
    assert len(service.experiments.trials(p["id"]))==2

def test_scoped_execution_preserves_economics_and_idempotent_budget(team):
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet,limit=1)
    item=run(service,author,p,key="approved-extra",grant=grant["id"])
    assert item["status"]=="COMPLETED",item
    original=service.experiments.trials(p["id"])[0]
    assert item["summary"]["economic_hash"]==original["summary"]["economic_hash"]
    retry=service.start(author,dict(request_id="approved-extra",protocol_id=p["id"],kind="BASELINE",grant_id=grant["id"]))
    assert retry["id"]==item["id"] and len(service.records("EXECUTION_RESERVED",grant["id"]))==1
    with pytest.raises(HTTPException,match="budget"):
        run(service,author,p,key="exhausted-extra",grant=grant["id"])
    exported=service.export(author,packet["id"],True,True)
    assert exported["historical"] and verify_packet(exported)["repeat_results_match"]

def test_wrong_protocol_or_scenario_cannot_spend_scope_budget(team):
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet,scenarios=["REPRODUCE"])
    with pytest.raises(HTTPException,match="Scenario"):
        run(service,author,p,key="wrong-scenario",grant=grant["id"])
    assert service.records("EXECUTION_RESERVED",grant["id"])==[]
    sample=service.example(author,"different-definition")
    other=service.freeze(author,dict(request_id="different-protocol",contract_id=sample["contract"]["id"],dataset_id=sample["dataset_id"],
        observation_cutoff="2026-11-06T15:00:09+05:30",allow_unverified_data=True,max_trials=8))
    with pytest.raises(HTTPException):
        run(service,author,other,kind="REPRODUCE",key="wrong-protocol",grant=grant["id"])
    assert service.records("EXECUTION_RESERVED",grant["id"])==[]

def test_revocation_stops_queued_operation_before_any_fills(team):
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet)
    item=service.start(author,dict(request_id="queued-revocation",protocol_id=p["id"],kind="BASELINE",grant_id=grant["id"]))
    service.revoke(reviewer,dict(request_id="revoke-queued",grant_id=grant["id"],comment="Stop before execution"))
    execute(service.workspace.research.root,item["run_id"])
    result=service.experiments.trial(item["id"])
    assert result["status"]=="CANCELLED" and "revoked" in result["error"]
    assert service.workspace.research.events(item["run_id"],kind="FILL")==[]

def test_revocation_during_execution_retains_partial_fills_and_costs(team,monkeypatch):
    from app.research.contract_replay import ContractReplay
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet)
    item=service.start(author,dict(request_id="mid-run-revocation",protocol_id=p["id"],kind="BASELINE",grant_id=grant["id"]))
    original=ContractReplay.step
    def revoke_after_entry(replay,rows,cursor,**kwargs):
        original(replay,rows,cursor,**kwargs)
        if cursor==3:service.revoke(reviewer,dict(request_id="revoke-after-fills",grant_id=grant["id"],comment="Stop after committed entry"))
    monkeypatch.setattr(ContractReplay,"step",revoke_after_entry)
    execute(service.workspace.research.root,item["run_id"])
    result=service.experiments.trial(item["id"])
    assert result["status"]=="CANCELLED"
    assert len(service.workspace.research.events(item["run_id"],kind="FILL"))==2
    captured=service.capture(author,dict(request_id="partial-capture",protocol_id=p["id"]))
    partial=service.export(author,captured["id"],True,True)["core"]["trials"][-1]
    assert partial["partial_execution"]["committed_fills"]==2 and partial["partial_execution"]["estimated_fees_paise"]>0

def test_expiry_and_role_change_stop_execution(team,monkeypatch):
    import app.team.gates as gates
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet)
    item=service.start(author,dict(request_id="expired-operation",protocol_id=p["id"],kind="BASELINE",grant_id=grant["id"]))
    auth=item["core"]["authorization"]
    assert "expired" in authority_reason(service.root,auth,now=datetime.fromisoformat(grant["core"]["expires_at"]).timestamp()+1)
    service.identity.change_member(author,reviewer["id"],["RESEARCHER"],True)
    execute(service.workspace.research.root,item["run_id"])
    assert service.experiments.trial(item["id"])["status"]=="CANCELLED"

def test_new_snapshot_decision_supersedes_old_scope(team):
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet)
    service.review(reviewer,dict(request_id="request-changes-later",packet_id=packet["id"],decision="REQUEST_CHANGES",comment="A material issue needs review"))
    with pytest.raises(HTTPException,match="superseded"):
        run(service,author,p,key="superseded-operation",grant=grant["id"])

def test_organization_stores_and_sessions_are_isolated(team,tmp_path):
    service,author,reviewer=team;p=prepare(service,author)
    other=Team(tmp_path/"other")
    try:
        other.identity.bootstrap((other.root/"bootstrap.key").read_text(),"Other","other@example.test",PASSWORD)
        token,csrf,user=service.identity.login(EMAIL,PASSWORD,"fixture")
        with pytest.raises(HTTPException):other.identity.authenticate(token)
        with pytest.raises(HTTPException) as error:other.protocol(p["id"])
        assert error.value.status_code==404
        assert other.identity.organization()["id"]!=service.identity.organization()["id"]
        assert other.workspace.research.catalog.datasets()==[]
    finally:other.close()

def test_session_expiry_and_membership_change_invalidate_existing_session(team):
    service,author,reviewer=team
    token,csrf,_=service.identity.login("reviewer@example.test",PASSWORD,"fixture")
    now=service.identity.clock()
    service.identity.clock=lambda:now+1801
    with pytest.raises(HTTPException):service.identity.authenticate(token)
    service.identity.clock=lambda:now
    token,csrf,_=service.identity.login("reviewer@example.test",PASSWORD,"fixture")
    service.identity.change_member(author,reviewer["id"],["REVIEWER"],False)
    with pytest.raises(HTTPException):service.identity.authenticate(token)

def test_identity_and_review_backup_integrity(team):
    service,author,reviewer=team;p,packet=evidence(service,author);scope(service,reviewer,p,packet)
    assert check_auth_database(service.identity.db)["users_verified"]==2
    assert check_review_database(service.workspace.db)["records_verified"]>=4
    with service.identity.db:
        service.identity.db.execute("UPDATE team_identity_users SET document=replace(document,'Independent reviewer','Tampered') WHERE id=?",(reviewer["id"],))
    with pytest.raises(ValueError,match="membership"):check_auth_database(service.identity.db)

def test_team_only_application_exposes_no_legacy_or_broker_routes(tmp_path):
    app=create_team_app(tmp_path/"standalone")
    with TestClient(app,base_url="http://127.0.0.1") as client:
        for path in ("/api/state","/api/orders","/api/pilot/status","/api/research/runs","/static/workspace-v020.html"):
            assert client.get(path).status_code==404,path
        assert client.get("/api/team/dashboard").status_code==401
        assert "frame-ancestors 'self'" in client.get("/team").headers["content-security-policy"]
        bad=client.post("/api/team/login",json=dict(email="x@example.test",password="private-sentinel",unexpected="private-sentinel"),headers=HEADERS)
        assert bad.status_code==422 and "private-sentinel" not in bad.text
        cross=client.post("/api/team/login",json=dict(email=EMAIL,password=PASSWORD),headers=dict(HEADERS,Origin="https://other.example"))
        assert cross.status_code==403


def test_password_change_and_invitation_reissue_invalidate_existing_sessions(team):
    service,author,reviewer=team
    token,csrf,_=service.identity.login("reviewer@example.test",PASSWORD,"fixture")
    invite=service.identity.renew_invite(author,reviewer["id"])
    with pytest.raises(HTTPException):service.identity.authenticate(token)
    with pytest.raises(HTTPException):service.identity.login("reviewer@example.test",PASSWORD,"fixture")
    new_password="Controlled replacement password 12345"
    new=service.identity.accept(invite["invitation_token"],new_password)
    token,csrf,_=service.identity.login(new["email"],new_password,"fixture")
    service.identity.change_password(new,new_password,"A third controlled password 67890")
    with pytest.raises(HTTPException):service.identity.authenticate(token)
    with pytest.raises(ValueError,match="last active"):service.identity.renew_invite(author,author["id"])


def test_login_throttling_does_not_echo_credentials(team):
    service,author,reviewer=team
    for _ in range(5):
        with pytest.raises(HTTPException) as error:service.identity.login("reviewer@example.test","A wrong controlled password", "throttle-fixture")
        assert error.value.status_code==401 and "wrong" not in error.value.detail
    with pytest.raises(HTTPException) as error:service.identity.login("reviewer@example.test",PASSWORD,"throttle-fixture")
    assert error.value.status_code==429


def test_changed_execution_config_fails_worker_authority(team):
    from app.contract_research import scenario_config
    service,author,reviewer=team;p=prepare(service,author)
    item=service.start(author,dict(request_id="bound-configuration",protocol_id=p["id"],kind="BASELINE",grant_id=None))
    config=scenario_config(p["core"],"BASELINE");config["capital"]+=100
    assert "differs" in authority_reason(service.root,item["core"]["authorization"],config=config)


def test_no_team_store_can_share_the_local_owner_runtime_directory(tmp_path):
    root=tmp_path/"owner";root.mkdir();(root/"paper.sqlite3").touch()
    with pytest.raises(ValueError,match="dedicated"):Team(root)


def change_member_in_process(root,actor,identifier,started,finished):
    from app.team.auth import Identity
    started.set()
    identity=Identity(root)
    try:
        identity.change_member(actor,identifier,["REVIEWER"],False)
        finished.set()
    finally:identity.close()

def test_operator_and_server_processes_serialize_authority_mutations(team):
    import multiprocessing
    service,author,reviewer=team
    context=multiprocessing.get_context("spawn")
    started,finished=context.Event(),context.Event()
    # Hold the same organization boundary used while a backup captures its databases.
    with service.lock:
        process=context.Process(target=change_member_in_process,args=(str(service.root),author,reviewer["id"],started,finished))
        process.start()
        # Startup integrity checking is also serialized, before the membership mutation.
        assert started.wait(10)
        assert not finished.wait(.4)
    process.join(15)
    if process.is_alive():process.terminate();process.join();raise AssertionError("Operator process did not finish")
    assert process.exitcode==0 and finished.is_set()
    assert not service.identity.user(reviewer["id"])["active"]
