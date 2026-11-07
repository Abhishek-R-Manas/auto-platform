"""MVP-4 golden financial behavior and fail-closed evidence presentation."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.research.catalog import digest
from app.research.jobs import CURRENT_BUILD
from app.team.api import create_team_app
from app.validation.benchmark import VERSION, cases, evaluate_case, summarize
from app.validation.evidence import catalog, read_evidence
from tools.validate_financial import GATES
from tools.workspace_fingerprint import fingerprints


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["id"])
def test_golden_financial_case(case):
    result = evaluate_case(case)
    assert result["passed"], result
    if case["id"] == "replay-partial":
        assert 65 in result["actual"]["fill_quantities"]
        assert result["actual"]["fill_count"] > 4


def controlled_report():
    """Envelope fixture for verifier fault injection, never financial execution evidence."""
    rows = [dict(c, actual=copy.deepcopy(c["expected"]), passed=True, mismatches={},
        expected_hash=digest(c["expected"]), evidence_kind="CONTROLLED_SYNTHETIC") for c in catalog()]
    core = dict(schema_version=VERSION, checked_at="2026-11-07T00:00:00+00:00", build=CURRENT_BUILD,
        identity=fingerprints(), case_catalog_hash=digest(catalog()), cases=rows, summary=summarize(rows),
        regression_gates=dict(tests=GATES, passed=True, exit_code=0), all_controlled_checks_passed=True,
        evidence_kind="CONTROLLED_SYNTHETIC", independent_review_verified=False, genuine_data_calibrated=False,
        genuine_ai_provider_calls=False, live_authority=False)
    return dict(core=core, report_hash=digest(core))


def write_report(tmp_path, report):
    path = tmp_path/"validation.json"
    path.write_text(json.dumps(report))
    return path


def test_missing_invalid_stale_and_current_reports(tmp_path):
    assert read_evidence(tmp_path/"absent.json")["status"] == "NOT_RUN"
    report = controlled_report()
    current = read_evidence(write_report(tmp_path, report))
    assert current["status"] == "PASS_CONTROLLED"
    assert not current["mvp4_accepted"] and not current["live_authority"]
    report["core"]["identity"]["sha256"] = "0"*64
    report["report_hash"] = digest(report["core"])
    stale = read_evidence(write_report(tmp_path, report))
    assert stale["status"] == "STALE" and not stale["controlled_checks_passed"]
    report["core"]["summary"]["passed"] = -1
    assert read_evidence(write_report(tmp_path, report))["status"] == "INVALID"


@pytest.mark.parametrize("fault", ["claimed_pass", "missing_case", "missing_gate", "external_acceptance", "summary", "changed_expected"])
def test_rehashed_inconsistent_report_cannot_claim_pass(tmp_path, fault):
    report = controlled_report()
    core = report["core"]
    if fault == "claimed_pass":
        core["cases"][0]["actual"] = {}
    elif fault == "missing_case":
        core["cases"].pop()
    elif fault == "missing_gate":
        core["regression_gates"]["tests"] = GATES[:-1]
    elif fault == "external_acceptance":
        core["independent_review_verified"] = True
    elif fault == "summary":
        core["summary"]["passed"] += 1
    else:
        core["cases"][0]["expected"]["max_loss_paise"] = 0
    report["report_hash"] = digest(core)
    result = read_evidence(write_report(tmp_path, report))
    assert result["status"] == "INVALID"
    assert not result["controlled_checks_passed"] and not result["mvp4_accepted"]


def test_financial_mutation_is_detected(monkeypatch):
    import app.validation.benchmark as module
    original = module.expiry_payoff
    def wrong(*args, **kwargs):
        result = original(*args, **kwargs)
        result["max_loss_paise"] += 1
        return result
    monkeypatch.setattr(module, "expiry_payoff", wrong)
    result = evaluate_case(cases()[0])
    assert not result["passed"] and "max_loss_paise" in result["mismatches"]


def test_case_exception_is_a_failure_not_fault_detection():
    case = next(c for c in cases() if c["label"] == "FAULT")
    def broken():
        raise ValueError("private sentinel must not enter report")
    case["execute"] = broken
    result = evaluate_case(case)
    assert not result["passed"] and result["error_type"] == "ValueError"
    assert "private sentinel" not in json.dumps(result)


def test_owner_validation_is_read_only(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTHA_LIVE_TRANSPORT_ENABLED", raising=False)
    with TestClient(create_app(tmp_path/"paper.sqlite3", background=False), base_url="http://127.0.0.1") as client:
        before = client.get("/api/state").json()
        result = client.get("/api/contract-research/validation")
        assert result.status_code == 200 and not result.json()["mvp4_accepted"]
        assert client.post("/api/contract-research/validation", json={}).status_code in {403, 405}
        after = client.get("/api/state").json()
        assert all(before[key] == after[key] for key in ("account", "orders", "positions"))


def test_report_download_preserves_numeric_types_and_hash(tmp_path, monkeypatch):
    import app.validation.evidence as evidence
    report = controlled_report()
    monkeypatch.setattr(evidence, "REPORT", write_report(tmp_path, report))
    monkeypatch.delenv("ARTHA_LIVE_TRANSPORT_ENABLED", raising=False)
    with TestClient(create_app(tmp_path/"paper.sqlite3", background=False), base_url="http://127.0.0.1") as client:
        response = client.get("/api/contract-research/validation/export")
        assert response.status_code == 200
        exported = json.loads(response.content)
        assert digest(exported["core"]) == exported["report_hash"] == report["report_hash"]
        assert type(exported["core"]["summary"]["clean_control_failure_rate"]) is float


def test_team_validation_requires_authenticated_member(tmp_path):
    application = create_team_app(tmp_path/"team")
    with TestClient(application, base_url="http://127.0.0.1") as client:
        assert client.get("/api/team/validation").status_code == 401
        assert client.get("/api/team/validation/export").status_code == 401
        headers = {"X-Artha-Client": "paper-workspace"}
        key = (application.state.team.root/"bootstrap.key").read_text()
        password = "Controlled validation password 1234"
        assert client.post("/api/team/bootstrap", headers=headers, json=dict(bootstrap_key=key,
            organization_name="Controlled", email="validation@example.test", password=password)).status_code == 200
        assert client.post("/api/team/login", headers=headers,
            json=dict(email="validation@example.test", password=password)).status_code == 200
        response = client.get("/api/team/validation")
        assert response.status_code == 200 and not response.json()["live_authority"]
        assert not application.state.team.workspace.research.catalog.datasets()
