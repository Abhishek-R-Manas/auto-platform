"""Run isolated MVP-4 goldens and boundary gates; atomically record actual outcomes."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.research.catalog import digest
from app.research.jobs import CURRENT_BUILD
from app.validation.benchmark import VERSION, cases, evaluate_case, summarize
from tools.workspace_fingerprint import fingerprints

GATES = [
    "tests/test_candidates.py::test_protocol_has_disjoint_immutable_windows_and_training_only_context",
    "tests/test_candidates.py::test_model_cannot_inject_execution_code_or_risk_overrides",
    "tests/test_candidates.py::test_screening_budget_preserves_one_final_holdout_run",
    "tests/test_contract_research.py::test_cancel_after_fills_preserves_committed_costs_and_partial_trace",
    "tests/test_team.py::test_snapshot_acceptance_is_independent_and_does_not_grant_execution",
    "tests/test_team.py::test_expiry_and_role_change_stop_execution",
    "tests/test_team.py::test_revocation_during_execution_retains_partial_fills_and_costs",
    "tests/test_team.py::test_organization_stores_and_sessions_are_isolated",
    "tests/test_team_recovery.py::test_backup_restore_preserves_evidence_and_requires_new_authority",
    "tests/test_team_recovery.py::test_paused_research_survives_isolated_restore_without_autoresume",
]


def validate():
    identity = fingerprints()
    results = []
    for case in cases():
        row = evaluate_case(case)
        results.append(row)
        print(json.dumps(dict(case=row["id"], passed=row["passed"], mismatches=row["mismatches"],
                              error_type=row.get("error_type"))), flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key in ("KITE_API_KEY", "KITE_API_SECRET", "KITE_ACCESS_TOKEN", "ARTHA_AI_API_KEY", "ARTHA_AI_MODEL",
                "OPENAI_API_KEY", "ARTHA_LIVE_TRANSPORT_ENABLED", "ARTHA_TELEGRAM_BOT_TOKEN", "ARTHA_TELEGRAM_CHAT_ID"):
        env.pop(key, None)
    process = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", *GATES],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=600)
    print(process.stdout[-3000:]+process.stderr[-1000:], flush=True)
    if fingerprints() != identity:
        raise ValueError("Source changed during validation; no new report published")
    from app.validation.evidence import catalog
    core = dict(schema_version=VERSION, checked_at=datetime.now(timezone.utc).isoformat(),
        build=CURRENT_BUILD, identity=identity, case_catalog_hash=digest(catalog()),
        cases=results, summary=summarize(results),
        regression_gates=dict(tests=GATES, passed=process.returncode == 0, exit_code=process.returncode),
        all_controlled_checks_passed=all(row["passed"] for row in results) and process.returncode == 0,
        evidence_kind="CONTROLLED_SYNTHETIC", independent_review_verified=False,
        genuine_data_calibrated=False, genuine_ai_provider_calls=False, live_authority=False,
        tolerance="Exact integer paise for declared monetary cases; exact boolean/integer boundary outcomes")
    report = dict(core=core, report_hash=digest(core))
    destination = ROOT/"docs/mvp-4/validation.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(report, stream, indent=2)
        stream.write("\n")
    temporary.replace(destination)
    print(json.dumps(dict(summary=core["summary"], passed=core["all_controlled_checks_passed"], report=str(destination))), flush=True)
    return core["all_controlled_checks_passed"]


if __name__ == "__main__":
    raise SystemExit(0 if validate() else 1)
