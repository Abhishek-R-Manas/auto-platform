"""Read-only worker authority checks using organization-owned paths, never client paths."""
from contextlib import closing
from datetime import datetime
import json,sqlite3,time
from pathlib import Path
from ..research.catalog import digest
from ..contract_research import verify_record
from ..research.jobs import CURRENT_BUILD

def authority_reason(root,auth,now=None,config=None):
    now=time.time() if now is None else now
    root=Path(root)
    try:
        with closing(sqlite3.connect((root/"identity.sqlite3").resolve().as_uri()+"?mode=ro",uri=True)) as identities, closing(sqlite3.connect((root/"workspace.sqlite3").resolve().as_uri()+"?mode=ro",uri=True)) as db:
            org=json.loads(identities.execute("SELECT document FROM team_identity_settings WHERE name='organization'").fetchone()[0])
            if auth["organization_id"]!=org["id"] or auth["generation"]!=org["generation"]:return "Organization recovery requires renewed authority"
            def user(identifier):
                raw=identities.execute("SELECT document FROM team_identity_users WHERE id=?",(identifier,)).fetchone()
                if not raw:raise ValueError("Missing authority member")
                item=json.loads(raw[0])
                event=identities.execute("SELECT document FROM team_identity_events WHERE json_extract(document,'$.subject_id')=? AND json_extract(document,'$.details.user_hash') IS NOT NULL ORDER BY sequence DESC LIMIT 1",(identifier,)).fetchone()
                if not event or json.loads(event[0])["details"]["user_hash"]!=digest(item):raise ValueError("Identity integrity failed")
                return item
            actor=user(auth["actor_id"])
            if not actor["active"] or actor["version"]!=auth["actor_version"] or "RESEARCHER" not in actor["roles"]:return "Researcher authority was removed or changed"
            def record(identifier):
                row=db.execute("SELECT document FROM team_review_records WHERE id=?",(identifier,)).fetchone()
                if not row:raise ValueError("Missing authority record")
                item=verify_record(json.loads(row[0]))
                if item["id"]!=identifier or item["core"]["organization_id"]!=org["id"]:raise ValueError("Authority record binding failed")
                return item["core"]
            if auth.get("operation_id"):
                operation=record(auth["operation_id"])
                if operation["kind"]!="EXECUTION_RESERVED" or operation["protocol_id"]!=auth["protocol_id"] or operation["scenario"]!=auth["scenario"] or operation["grant_id"]!=auth["grant_id"] or operation["authenticated_context"]["actor_id"]!=actor["id"]:
                    return "Operation differs from its authorized reservation"
            protocol=verify_record(json.loads(db.execute("SELECT document FROM mvp_protocols WHERE id=?",(auth["protocol_id"],)).fetchone()[0]))
            p=protocol["core"]
            if config is not None:
                from ..contract_research import scenario_config
                if config!=scenario_config(p,auth["scenario"]):return "Execution differs from its approved protocol"
            if p["build"]!=CURRENT_BUILD:return "Operating scope was bound to another engine build"
            if not auth["grant_id"]:
                if db.execute("SELECT 1 FROM mvp_packets WHERE protocol_id=?",(protocol["id"],)).fetchone():return "Further execution requires an independent operating scope"
                return None
            grant=record(auth["grant_id"])
            if grant["kind"]!="SCOPE_GRANT" or grant["generation"]!=org["generation"]:return "Operating scope is unavailable after recovery"
            if datetime.fromisoformat(grant["expires_at"]).timestamp()<=now:return "Operating scope expired"
            if auth["scenario"] not in grant["scenarios"]:return "Scenario is outside the approved operating scope"
            for raw, in db.execute("SELECT document FROM team_review_records WHERE kind='SCOPE_REVOKED' AND parent_id=?",(auth["grant_id"],)):
                verify_record(json.loads(raw));return "Operating scope revoked"
            reviewer=user(grant["authenticated_context"]["actor_id"])
            if not reviewer["active"] or "REVIEWER" not in reviewer["roles"] or reviewer["version"]!=grant["authenticated_context"]["actor_version"]:return "Scope reviewer authority changed"
            review=record(grant["review_id"])
            latest=db.execute("SELECT id FROM team_review_records WHERE kind='SNAPSHOT_REVIEW' AND parent_id=? ORDER BY rowid DESC LIMIT 1",(grant["packet_id"],)).fetchone()
            if not latest or latest[0]!=grant["review_id"] or review["decision"]!="ACCEPT":return "Snapshot review was superseded"
            owner=user(review["authenticated_context"]["actor_id"])
            if not owner["active"] or "REVIEWER" not in owner["roles"] or owner["version"]!=review["authenticated_context"]["actor_version"]:return "Snapshot reviewer authority changed"
            if auth["actor_id"]==grant["authenticated_context"]["actor_id"] or grant["authenticated_context"]["actor_id"] in review["contributors"]:return "Independent review separation failed"
            expected=dict(protocol_id=protocol["id"],contract_id=p["contract_id"],definition_hash=p["definition_hash"],
                dataset_id=p["dataset_snapshot"]["id"],dataset_hash=p["dataset_snapshot"]["parquet_hash"],build_hash=p["build"]["code_hash"],risk_policy_hash=p["risk_policy_hash"])
            if grant["bindings"]!=expected:return "Operating scope binding mismatch"
            used=[verify_record(json.loads(r[0])) for r in db.execute("SELECT document FROM team_review_records WHERE kind='EXECUTION_RESERVED' AND parent_id=? ORDER BY rowid",(auth["grant_id"],))]
            if auth.get("operation_id"):
                if not any(item["id"]==auth["operation_id"] for item in used[:grant["max_new_trials"]]):return "Operating scope attempt budget exhausted"
            elif len(used)>=grant["max_new_trials"]:return "Operating scope attempt budget exhausted"
            return None
    except (OSError,sqlite3.Error,KeyError,TypeError,ValueError,AttributeError):
        return "Research authority could not be verified"
