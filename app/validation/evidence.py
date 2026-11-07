"""Read-only, build-bound software evidence for owner and authenticated team views."""
import json
from pathlib import Path

from app.research.catalog import canonical, digest
from app.research.jobs import CURRENT_BUILD
from app.validation.benchmark import VERSION, cases, summarize
from tools.workspace_fingerprint import fingerprints

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT/"docs/mvp-4/validation.json"
DEPENDENCIES = [
    dict(id="quant", status="PENDING", title="Independent financial reviewer",
         detail="A separate qualified reviewer must inspect the expected calculations, tolerances and actual outcomes for this exact build."),
    dict(id="market", status="PENDING", title="Genuine-data calibration",
         detail="A permitted, provenance-checked dataset and broker or exchange comparisons are needed for spread, depth, latency, fills and dated charges."),
    dict(id="source", status="PENDING", title="Customer and source acceptance",
         detail="Carry forward the actual workflow, data rights and receipt-time acceptance requirements from MVP-1 through MVP-3."),
    dict(id="operations", status="PENDING", title="Customer deployment and off-host recovery",
         detail="An approved SSH destination, pinned encryption recipient, actual external restore and public team TLS acceptance remain open."),
    dict(id="ai", status="NOT_OFFERED", title="Genuine AI explanation",
         detail="The MVP team flow offers deterministic research, not an AI explanation service. Any later AI offering requires genuine-provider evidence; legacy gates remain in force."),
]


def catalog():
    return [{key: case[key] for key in ("id", "category", "label", "expected", "rationale")} for case in cases()]


def export_evidence():
    report = read_evidence()["report"]
    if report is None:
        raise ValueError("No verified validation report is available")
    return canonical(report)


def read_evidence(path=None):
    result = dict(schema_version=VERSION, status="NOT_RUN", current=False, controlled_checks_passed=False,
        mvp4_accepted=False, live_authority=False, independent_review_verified=False,
        genuine_data_calibrated=False, dependencies=DEPENDENCIES, report=None,
        message="No recorded validation. Ask the operator to run the MVP-4 validator.")
    try:
        location = Path(path) if path is not None else REPORT
        if not location.exists():
            return result
        if location.stat().st_size > 4*1024*1024:
            raise ValueError("Oversized report")
        report = json.loads(location.read_text())
        core = report["core"]
        if any(core[key] is not False for key in ("independent_review_verified", "genuine_data_calibrated", "genuine_ai_provider_calls", "live_authority")) or core["evidence_kind"] != "CONTROLLED_SYNTHETIC":
            raise ValueError("Controlled report cannot assert external acceptance")
        if report["report_hash"] != digest(core) or core["schema_version"] != VERSION:
            raise ValueError("Report integrity mismatch")
        expected = catalog()
        if core["case_catalog_hash"] != digest(expected):
            return dict(result, status="STALE", message="The benchmark changed. Run validation again for this build.")
        rows = core["cases"]
        if [r["id"] for r in rows] != [c["id"] for c in expected]:
            raise ValueError("Incomplete or duplicate case inventory")
        for row, case in zip(rows, expected):
            if any(row[key] != case[key] for key in ("id", "category", "label", "expected", "rationale")):
                raise ValueError("Changed expected case")
            passed = not row.get("error_type") and all(row["actual"].get(key) == value for key, value in case["expected"].items())
            if row["passed"] is not passed or row["expected_hash"] != digest(case["expected"]):
                raise ValueError("Inconsistent case outcome")
        if core["summary"] != summarize(rows):
            raise ValueError("Inconsistent summary")
        gates = core["regression_gates"]
        from tools.validate_financial import GATES
        if gates["tests"] != GATES or type(gates["passed"]) is not bool or gates["passed"] != (gates["exit_code"] == 0):
            raise ValueError("Incomplete permission/recovery/information gates")
        current = core["build"] == CURRENT_BUILD and core["identity"] == fingerprints()
        passed = all(r["passed"] for r in rows) and gates["passed"]
        if core["all_controlled_checks_passed"] is not passed:
            raise ValueError("Inconsistent validation outcome")
        return dict(result, status=("PASS_CONTROLLED" if passed else "FAIL") if current else "STALE",
            current=current, controlled_checks_passed=passed and current, report=report,
            message="Controlled software cases passed. Independent review and genuine-data calibration remain pending." if current and passed
                else "Checks failed. Inspect the cases before relying on this build." if current
                else "This report covers an earlier source snapshot. Run validation again.")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return dict(result, status="INVALID", message="Validation evidence could not be verified. Ask the operator to rerun validation.")
