"""MVP-2 frozen research protocols and private, hash-verifiable evidence packets."""
import copy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import Field, StrictBool, StrictInt, field_validator

from .contracts import ContractBook, Definition, Hash
from .research.catalog import aware, canonical, digest
from .research.jobs import CURRENT_BUILD, open_job
from .research.schemas import Strict, Risk, Run

VERSION = "mvp2/1.0"
ACTIVE = {"QUEUED", "RUNNING", "PAUSED", "RESERVED"}
MAX_TRACE_EVENTS = 20000
MAX_PACKET_BYTES = 16*1024*1024


class Freeze(Strict):
    request_id: Annotated[str, Field(min_length=8, max_length=100)]
    contract_id: Hash
    dataset_id: Hash
    organization_ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")] = "LOCAL_WORKSPACE"
    capital_paise: Annotated[StrictInt, Field(ge=10000, le=100000000000)] = 1000000000
    risk_policy: Risk = Risk()
    observation_cutoff: datetime
    entry_window_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 10
    latency_seconds: Annotated[StrictInt, Field(ge=0, le=60)] = 0
    max_trials: Annotated[StrictInt, Field(ge=2, le=20)] = 6
    allow_unverified_data: StrictBool = False
    external_references: Annotated[list[Annotated[str, Field(min_length=1, max_length=500)]], Field(max_length=10)] = []
    _cutoff = field_validator("observation_cutoff")(aware)


class Start(Strict):
    request_id: Annotated[str, Field(min_length=8, max_length=100)]
    protocol_id: Hash
    kind: Literal["BASELINE", "REPRODUCE", "COST_STRESS"]


class Capture(Strict):
    request_id: Annotated[str, Field(min_length=8, max_length=100)]
    protocol_id: Hash


class TrialControl(Strict):
    action: Literal["PAUSE", "RESUME", "CANCEL"]


class ExampleRequest(Strict):
    request_id: Annotated[str,Field(min_length=8,max_length=90)]


class PacketInput(Strict):
    packet: dict
    expected_protocol: Hash|None=None
    expected_build: Hash|None=None
    expected_dataset: Hash|None=None


def hashed_record(core):
    return dict(id=digest(core), core=core, created_at=datetime.now(timezone.utc).isoformat())


def verify_record(item):
    if item["id"] != digest(item["core"]):
        raise ValueError("Research evidence integrity failed")
    return item



def scenario_config(core, kind):
    config=copy.deepcopy(core["run_config"])
    if kind=="COST_STRESS":
        config["risk"]["slippage_bps"]=max(1,config["risk"]["slippage_bps"]*2)
        config["contract_protocol"]["fee_multiplier"]=2
        config["execution"]["latency_seconds"]+=1
    elif kind not in {"BASELINE","REPRODUCE"}:
        raise ValueError("Unsupported research scenario")
    return Run.model_validate(config).model_dump(mode="json")


def check_research_database(db):
    """Read-only verification for backup/restore."""
    tables={"PROTOCOL":"mvp_protocols","TRIAL":"mvp_trials","PACKET":"mvp_packets"}
    records={}
    for kind,table in tables.items():
        records[kind]={}
        for identifier,raw in db.execute("SELECT id,document FROM "+table):
            item=verify_record(json.loads(raw))
            if item["id"]!=identifier:raise ValueError("Research backup identity integrity failed")
            records[kind][identifier]=item
    for item in records["TRIAL"].values():
        core=item["core"];protocol=records["PROTOCOL"].get(core["protocol_id"])
        if not protocol or digest(core["config"])!=core["config_hash"] or core["config"]!=scenario_config(protocol["core"],core["kind"]):
            raise ValueError("Research trial protocol/configuration integrity failed")
    for item in records["PACKET"].values():
        if item["core"]["protocol_id"] not in records["PROTOCOL"] or not verify_packet(item["core"]["packet"])["integrity_valid"]:
            raise ValueError("Research packet backup integrity failed")
    for expected,identifier,kind in db.execute("SELECT input_hash,object_id,kind FROM mvp_research_requests"):
        item=records.get(kind,{}).get(identifier)
        if not item:raise ValueError("Research request reference integrity failed")
        core=item["core"]
        payload=core["request"] if kind=="PROTOCOL" else {key:core[key] for key in ("protocol_id","kind")} if kind=="TRIAL" else dict(protocol_id=core["protocol_id"])
        if digest(payload)!=expected:raise ValueError("Research request binding integrity failed")
    previous="0"*64;count=0
    for sequence,raw in db.execute("SELECT sequence,document FROM mvp_research_events ORDER BY sequence"):
        event=json.loads(raw);expected=event.pop("hash");count+=1
        if sequence!=count or event["sequence"]!=count or event["previous_hash"]!=previous or digest(event)!=expected:
            raise ValueError("Research backup event-chain integrity failed")
        previous=expected
    return dict(protocols_verified=len(records["PROTOCOL"]),trials_verified=len(records["TRIAL"]),packets_verified=len(records["PACKET"]),events_verified=count)


def verify_packet(packet, expected_protocol=None, expected_build=None, expected_dataset=None):
    """Data only: no source execution, network requests or paths from the packet."""
    invalid = dict(integrity_valid=False, trace_valid=False, repeat_results_match=False,
                   source_authenticity_verified=False, live_authority=False)
    try:
        if not isinstance(packet, dict) or packet.get("schema_version") != VERSION:
            return dict(invalid, reason="Unsupported evidence packet format")
        core = packet["core"]
        if digest(core) != packet["packet_hash"]:
            return dict(invalid, reason="Packet hash mismatch")
        protocol = verify_record(core["protocol"])
        config = Run.model_validate(protocol["core"]["run_config"]).model_dump(mode="json")
        if digest(config) != protocol["core"]["run_config_hash"]:
            return dict(invalid, reason="Protocol configuration mismatch")
        if digest(protocol["core"]["definition"]) != config["contract_protocol"]["definition_hash"]:
            return dict(invalid, reason="Definition interpretation mismatch")
        if config["contract_protocol"]["contract_id"]!=protocol["core"]["contract_id"] or digest(config["risk"])!=protocol["core"]["risk_policy_hash"]:
            return dict(invalid,reason="Protocol strategy or risk binding mismatch")
        if config["dataset_id"] != protocol["core"]["dataset_snapshot"]["id"]:
            return dict(invalid, reason="Dataset binding mismatch")
        for expected, actual in ((expected_protocol, protocol["id"]),
                                 (expected_build, protocol["core"]["build"]["code_hash"]),
                                 (expected_dataset, config["dataset_id"])):
            if expected is not None and expected != actual:
                return dict(invalid, reason="Expected identity does not match evidence")
        traces = packet.get("attachments", {})
        trace_valid = bool(traces)
        hashes = {}
        for trial in core["trials"]:
            if trial["status"] not in {"COMPLETED","FAILED","CANCELLED"}:
                return dict(invalid,reason="Packet contains a nonterminal attempt")
            if (trial["report"] is not None)!=(trial["status"]=="COMPLETED"):
                return dict(invalid,reason="Attempt report/status mismatch")
            expected_config=scenario_config(protocol["core"],trial["kind"])
            if trial["config_hash"]!=digest(expected_config):
                return dict(invalid,reason="Trial differs from its predeclared scenario")
            summary=trial["report"]
            if trial["status"]=="COMPLETED":
                if summary["config"]!=expected_config or summary["config_hash"]!=trial["config_hash"]:
                    return dict(invalid,reason="Trial differs from its predeclared scenario")
                if digest(summary["config"])!=summary["config_hash"] or summary["dataset_id"]!=config["dataset_id"]:
                    return dict(invalid,reason="Trial configuration or dataset mismatch")
                if summary["dataset_hash"]!=protocol["core"]["dataset_snapshot"]["parquet_hash"]:
                    return dict(invalid,reason="Trial dataset snapshot mismatch")
                if summary["environment"]!=protocol["core"]["build"]:
                    return dict(invalid,reason="Trial build mismatch")
            trace=traces.get(trial["id"])
            if trace is None:
                trace_valid=False
                continue
            if digest(trace)!=trial["trace_hash"]:
                return dict(invalid,reason="Execution trace hash mismatch")
            fills=summary["diagnostics"]["fills"] if summary else trial["partial_execution"]["committed_fills"]
            if len(trace["fills"])!=fills:
                return dict(invalid,reason="Execution fill count mismatch")
            previous="0"*64
            indexed={}
            for number,event in enumerate(trace["event_index"],1):
                head=digest(dict(previous_hash=previous,sequence=number,event_hash=event["event_hash"]))
                if event["sequence"]!=number or head!=event["head"]:
                    return dict(invalid,reason="Execution event chain mismatch")
                indexed[number]=event["event_hash"];previous=head
            if trace["event_index_complete"] and (previous!=trace["event_head"] or previous!=trial["event_head"]):
                return dict(invalid,reason="Execution event head mismatch")
            for fill in trace["fills"]:
                if fill["sequence"] in indexed and indexed[fill["sequence"]]!=digest(fill):
                    return dict(invalid,reason="Fill event binding mismatch")
            if summary:
                calculated=digest(dict(code_hash=summary["environment"]["code_hash"],dataset=summary["dataset_id"],
                    config=summary["config"],curve=trace["curve"],fills=trace["fills"]))
                if calculated!=summary["economic_hash"]:
                    return dict(invalid,reason="Economic trace mismatch")
                hashes.setdefault(trial["kind"],[]).append(calculated)
        repeated_hashes=hashes.get("BASELINE",[])+hashes.get("REPRODUCE",[])
        repeat=bool(hashes.get("BASELINE") and hashes.get("REPRODUCE") and len(set(repeated_hashes))==1)
        return dict(integrity_valid=True, trace_valid=trace_valid, repeat_results_match=repeat,
                    source_authenticity_verified=False, live_authority=False, protocol_id=protocol["id"],
                    reason="Local hashes and included traces are self-consistent; they do not authenticate the source or establish investment performance")
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        return dict(invalid, reason="Malformed or inconsistent evidence")


class ContractResearch:
    def __init__(self, workspace, book=None):
        self.workspace = workspace
        self.book = book or ContractBook(workspace)
        self.research = workspace.research
        with workspace.lock, workspace.db:
            workspace.db.executescript("""
              CREATE TABLE IF NOT EXISTS mvp_protocols(id TEXT PRIMARY KEY, document TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS mvp_trials(id TEXT PRIMARY KEY, protocol_id TEXT NOT NULL, document TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS mvp_packets(id TEXT PRIMARY KEY, protocol_id TEXT NOT NULL, document TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS mvp_research_requests(request_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, object_id TEXT NOT NULL, kind TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS mvp_research_events(sequence INTEGER PRIMARY KEY, document TEXT NOT NULL);
            """)

    def _event(self, kind, identifier, details):
        row = self.workspace.db.execute("SELECT document FROM mvp_research_events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = json.loads(row[0]) if row else None
        event = dict(sequence=previous["sequence"]+1 if previous else 1, previous_hash=previous["hash"] if previous else "0"*64,
                     recorded_at=datetime.now(timezone.utc).isoformat(), kind=kind, object_id=identifier, details=details)
        event["hash"] = digest(event)
        self.workspace.db.execute("INSERT INTO mvp_research_events VALUES(?,?)", (event["sequence"], canonical(event)))
        return event

    def audit(self):
        previous, count = "0"*64, 0
        with self.workspace.lock:
            for sequence, raw in self.workspace.db.execute("SELECT sequence, document FROM mvp_research_events ORDER BY sequence"):
                event = json.loads(raw)
                expected = event.pop("hash")
                count += 1
                if sequence != count or event["sequence"] != count or event["previous_hash"] != previous or digest(event) != expected:
                    raise ValueError("Research event-chain integrity failed")
                previous = expected
        return dict(ok=True, count=count, head=previous, externally_anchored=False)

    def _get(self, table, identifier):
        # Table names are internal literals, not client-controlled.
        with self.workspace.lock:
            row = self.workspace.db.execute("SELECT document FROM "+table+" WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("Research evidence record not found")
        item = verify_record(json.loads(row[0]))
        if item["id"] != identifier:
            raise ValueError("Research evidence identity integrity failed")
        return item

    def _request(self, request_id, kind, payload):
        row = self.workspace.db.execute("SELECT input_hash, object_id, kind FROM mvp_research_requests WHERE request_id=?", (request_id,)).fetchone()
        if row:
            if row[0] != digest(payload) or row[2] != kind:
                raise ValueError("Request id binds different research inputs")
            return row[1]
        return None

    def _bind(self, request_id, kind, payload, identifier):
        self.workspace.db.execute("INSERT INTO mvp_research_requests VALUES(?,?,?,?)", (request_id, digest(payload), identifier, kind))

    def get_protocol(self, identifier):
        return self._get("mvp_protocols", identifier)

    def protocols(self):
        with self.workspace.lock:
            ids = [r[0] for r in self.workspace.db.execute("SELECT id FROM mvp_protocols ORDER BY rowid DESC LIMIT 200")]
        return [self.get_protocol(i) for i in ids]

    def _dataset(self, identifier):
        dataset = self.research.catalog.verify(identifier)
        if digest(dict(manifest=dataset["manifest"], raw_hash=dataset["raw_hash"])) != identifier:
            raise ValueError("Dataset manifest identity integrity failed")
        return dataset

    def _current(self, protocol):
        if protocol["core"]["build"] != CURRENT_BUILD:
            raise ValueError("Protocol was frozen against another build; freeze a new protocol")
        dataset = self._dataset(protocol["core"]["dataset_snapshot"]["id"])
        if dataset["parquet_hash"] != protocol["core"]["dataset_snapshot"]["parquet_hash"]:
            raise ValueError("Frozen dataset snapshot changed")
        self.book.get(protocol["core"]["contract_id"])
        self.audit()
        return dataset

    def freeze(self, body):
        request = Freeze.model_validate(body)
        payload = request.model_dump(mode="json", exclude={"request_id"})
        with self.workspace.lock:
            prior = self._request(request.request_id, "PROTOCOL", payload)
            if prior:
                return self.get_protocol(prior)
        contract = self.book.get(request.contract_id)
        definition = Definition.model_validate(contract["core"]["definition"]).model_dump(mode="json")
        report = self.book.check(definition, request.dataset_id)
        if not report["definition_ready"]:
            raise ValueError("Correct the strategy definition before freezing an evaluation")
        dataset = self._dataset(request.dataset_id)
        manifest = dataset["manifest"]
        hard = {"DATASET_STATUS", "RAW_PRICES", "DATA_CONTRACT_TERMS", "DATA_CALENDAR", "DATED_FEES"}
        if any(f["code"] in hard and f["status"] == "BLOCKED" for f in report["findings"]):
            raise ValueError("Dataset identity, dated terms, calendar or ingestion checks failed")
        if manifest["frequency"] != "QUOTE" or definition["data"]["required_fidelity"] != "QUOTE":
            raise ValueError("This fixed-basket adapter requires observed quote/depth data; bar-only execution remains unsupported")
        entry, exit_at = aware(definition["rules"]["entry_at"]), aware(definition["rules"]["exit_at"])
        deadline = entry+timedelta(seconds=request.entry_window_seconds)
        if deadline > exit_at or not exit_at <= request.observation_cutoff < aware(definition["strategy"]["legs"][0]["expiry"]):
            raise ValueError("Observation cutoff/entry window must respect the frozen exit and expiry")
        from zoneinfo import ZoneInfo
        if request.observation_cutoff.astimezone(ZoneInfo("Asia/Kolkata")).date() != entry.astimezone(ZoneInfo("Asia/Kolkata")).date():
            raise ValueError("Observation cutoff must stay in the declared session date")
        if request.latency_seconds >= request.entry_window_seconds:
            raise ValueError("Execution latency must fit the entry window")
        required = {definition["strategy"]["reference_id"], *(l["instrument_id"] for l in definition["strategy"]["legs"])}
        rows = [r for r in self.research.catalog.rows(request.dataset_id) if r["instrument_id"] in required]
        if len(rows) > 50000:
            raise ValueError("Reduce this single-session evaluation to at most 50,000 relevant observations")
        if any(r.get("received_at") or r.get("available_at") for r in rows) and not all(r.get("received_at") and r.get("available_at") for r in rows):
            raise ValueError("Mixed receipt-clock coverage is unsupported; complete or explicitly omit arrival evidence")
        clock = "RECORDED_RECEIPT" if rows and all(r.get("received_at") and r.get("available_at") for r in rows) else "ASSUMED_EXCHANGE"
        unverified = [f for f in report["findings"] if f["status"] != "PASS"]
        if unverified and not request.allow_unverified_data:
            raise ValueError("Acknowledge unqualified research with incomplete data/customer evidence before proceeding")
        from .research.catalog import bounds
        session=bounds(manifest["calendar"],entry.astimezone(ZoneInfo("Asia/Kolkata")).date())
        if not session or request.observation_cutoff>session[1]:raise ValueError("Cutoff must stay within the declared market session")
        s = definition["strategy"]
        cp = dict(contract_id=request.contract_id, definition_hash=digest(definition), entry_at=entry, exit_at=exit_at,
                  entry_deadline=deadline, reference_id=s["reference_id"], units=s["lots"]*s["lot_size"], lot_size=s["lot_size"],
                  tick_size_paise=s["tick_size_paise"], min_depth_units=definition["data"]["min_depth_lots"]*s["lot_size"],
                  max_spread_bps=definition["data"]["max_spread_bps"], max_expiry_loss_paise=definition["risk"]["max_expiry_loss_paise"],
                  estimated_costs_paise=definition["risk"]["estimated_costs_paise"], clock_mode=clock)
        strategy = dict(id="contract-basket", name=definition["name"][:120], version=digest(definition), family="SCHEDULED",
                        signal_instruments=[s["reference_id"]], legs=[dict(instrument_id=l["instrument_id"], ratio=1 if l["side"]=="BUY" else -1, product="DERIVATIVE") for l in s["legs"]],
                        size_mode="LOTS", lots=s["lots"], allocation_pct=100, stop_pct=100, target_multiple=None, allow_short=True,
                        allow_overnight=False, roll_on_expiry=False, exit_dte_days=0,
                        schedule=[dict(at=entry, action="ENTER"), dict(at=exit_at, action="EXIT")])
        study_key = digest(dict(request=payload, definition=definition, dataset=dataset["parquet_hash"], build=CURRENT_BUILD))
        config = Run.model_validate(dict(dataset_id=request.dataset_id, name=definition["name"][:120], capital=request.capital_paise/100,
            strategies=[strategy], risk=request.risk_policy, start=entry, end=request.observation_cutoff, benchmark_id=s["reference_id"],
            experiment_id="mvp2-"+study_key[:40], contract_protocol=cp,
            execution=dict(max_quote_age_seconds=definition["data"]["max_quote_age_seconds"], latency_seconds=request.latency_seconds,
                           cancel_latency_seconds=0, allow_uncovered_options=False))).model_dump(mode="json")
        core = dict(schema_version=VERSION, request=payload, organization_ref=request.organization_ref,
                    identity_assurance="LOCAL_OWNER_CONTEXT_ONLY", contract_id=request.contract_id, definition=definition,
                    definition_hash=digest(definition), dataset_snapshot=report["dataset_snapshot"], build=copy.deepcopy(CURRENT_BUILD),
                    risk_policy_hash=digest(config["risk"]), run_config=config, run_config_hash=digest(config),
                    observation_cutoff=request.observation_cutoff.isoformat(), max_trials=request.max_trials,
                    data_findings=report["findings"], clock_mode=clock, live_orders_enabled=False)
        record = hashed_record(core)
        with self.workspace.lock, self.workspace.db:
            self.audit()
            prior = self._request(request.request_id, "PROTOCOL", payload)
            if prior:
                return self.get_protocol(prior)
            if len(self.protocols()) >= 200:
                raise ValueError("Review protocol retention before creating more than 200 protocols")
            self.workspace.db.execute("INSERT OR IGNORE INTO mvp_protocols VALUES(?,?)", (record["id"], canonical(record)))
            self._bind(request.request_id, "PROTOCOL", payload, record["id"])
            self._event("PROTOCOL_FROZEN", record["id"], dict(core_hash=record["id"]))
        return self.get_protocol(record["id"])

    def trials(self, protocol_id):
        self.get_protocol(protocol_id)
        with self.workspace.lock:
            ids = [r[0] for r in self.workspace.db.execute("SELECT id FROM mvp_trials WHERE protocol_id=? ORDER BY rowid", (protocol_id,))]
        return [self.trial(i) for i in ids]

    def trial(self, identifier):
        record = self._get("mvp_trials", identifier)
        key = "mvp2:"+identifier
        run_id = __import__("hashlib").sha256(key.encode()).hexdigest()[:32]
        try:
            run = self.research.get(run_id)
        except ValueError:
            return dict(record, run_id=None, status="RESERVED", error="Dispatch is not attached; recover this exact attempt")
        if run.get("dispatch_key") != key or run["config_hash"] != record["core"]["config_hash"] or digest(run["config"]) != record["core"]["config_hash"] or run["engine_hash"] != record["core"]["build_hash"]:
            raise ValueError("Research trial/run binding integrity failed")
        summary=None
        if run["status"]=="COMPLETED":
            with closing(open_job(self.research.path(run_id))) as db:
                report=json.loads(db.execute("SELECT document FROM report WHERE id=1").fetchone()[0])
            if report["config_hash"]!=record["core"]["config_hash"] or report["economic_hash"]!=run.get("economic_hash"):raise ValueError("Trial report binding integrity failed")
            summary=dict(net_pnl=report["account"]["equity"]-report["config"]["capital"],fees=report["diagnostics"]["estimated_fees"],
                fills=report["diagnostics"]["fills"],positions=len(report["positions"]),pending_orders=len(report["pending_orders"]),economic_hash=report["economic_hash"])
        return dict(record,run_id=run_id,status=run["status"],error=run.get("error"),progress=run.get("progress",0),summary=summary)

    def start(self, body):
        request = Start.model_validate(body)
        payload = request.model_dump(mode="json", exclude={"request_id"})
        protocol = self.get_protocol(request.protocol_id)
        self._current(protocol)
        with self.workspace.lock, self.workspace.db:
            prior = self._request(request.request_id, "TRIAL", payload)
            if prior:
                return self.dispatch(prior)
            trials = self.trials(request.protocol_id)
            if len(trials) >= protocol["core"]["max_trials"]:
                raise ValueError("Frozen trial budget exhausted; unsuccessful and cancelled attempts still count")
            if request.kind == "REPRODUCE" and not any(t["core"]["kind"]=="BASELINE" and t["status"]=="COMPLETED" for t in trials):
                raise ValueError("Complete a baseline before requesting its reproduction")
            config = scenario_config(protocol["core"],request.kind)
            core = dict(protocol_id=request.protocol_id, kind=request.kind, nonce=uuid.uuid4().hex,
                        config=config, config_hash=digest(config), build_hash=CURRENT_BUILD["code_hash"])
            record = hashed_record(core)
            self.workspace.db.execute("INSERT INTO mvp_trials VALUES(?,?,?)", (record["id"], request.protocol_id, canonical(record)))
            self._bind(request.request_id, "TRIAL", payload, record["id"])
            self._event("TRIAL_RESERVED", record["id"], dict(protocol_id=request.protocol_id, config_hash=core["config_hash"]))
        return self.dispatch(record["id"])

    def dispatch(self, identifier):
        record = self._get("mvp_trials", identifier)
        self._current(self.get_protocol(record["core"]["protocol_id"]))
        try:
            run = self.research.create(record["core"]["config"], dispatch_key="mvp2:"+identifier)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "Research dispatch interrupted; recover the same attempt"
            with self.workspace.lock, self.workspace.db:
                self._event("DISPATCH_INTERRUPTED", identifier, dict(reason=message[:500]))
            raise ValueError(message) from None
        with self.workspace.lock, self.workspace.db:
            self._event("TRIAL_DISPATCHED", identifier, dict(run_id=run["id"], config_hash=record["core"]["config_hash"]))
        return self.trial(identifier)

    def control(self, identifier, action):
        record = self.trial(identifier)
        self._current(self.get_protocol(record["core"]["protocol_id"]))
        if not record["run_id"]:
            raise ValueError("Recover the reserved attempt before controlling it")
        self.research.control(record["run_id"], action)
        with self.workspace.lock, self.workspace.db:
            self._event("TRIAL_CONTROL", identifier, dict(action=action, run_id=record["run_id"]))
        return self.trial(identifier)

    def capture(self, body):
        request = Capture.model_validate(body)
        payload = request.model_dump(mode="json", exclude={"request_id"})
        protocol = self.get_protocol(request.protocol_id)
        self._current(protocol)
        with self.workspace.lock:
            prior = self._request(request.request_id, "PACKET", payload)
            if prior:
                return self._get("mvp_packets", prior)
        trials = self.trials(request.protocol_id)
        if not trials or any(t["status"] in ACTIVE for t in trials):
            raise ValueError("Finish or cancel every attempt before capturing a fixed evidence packet")
        records, attachments = [], {}
        for item in trials:
            record = dict(id=item["id"], kind=item["core"]["kind"], run_id=item["run_id"], status=item["status"],
                          error=item.get("error"), config_hash=item["core"]["config_hash"], report=None, trace_hash=None)
            if item["run_id"]:
                with closing(open_job(self.research.path(item["run_id"]))) as db:
                    report_row=db.execute("SELECT document FROM report WHERE id=1").fetchone()
                    report=json.loads(report_row[0]) if item["status"]=="COMPLETED" and report_row else None
                    fills = [json.loads(r[0]) for r in db.execute("SELECT document FROM events WHERE kind='FILL' ORDER BY sequence")]
                    curve = [dict(time=r[0], equity=r[1]) for r in db.execute("SELECT time,json_extract(document,'$.account.equity') FROM events WHERE kind='EQUITY' ORDER BY sequence")]
                    index, head = [], "0"*64
                    for sequence, raw in db.execute("SELECT sequence,document FROM events ORDER BY sequence"):
                        event_hash = digest(json.loads(raw))
                        head = digest(dict(previous_hash=head, sequence=sequence, event_hash=event_hash))
                        if len(index)<MAX_TRACE_EVENTS:
                            index.append(dict(sequence=sequence, event_hash=event_hash, head=head))
                    trace = dict(fills=fills, curve=curve, event_index=index, event_head=head,
                                 event_index_complete=not index or sequence<=MAX_TRACE_EVENTS)
                record.update(report=report,trace_hash=digest(trace),event_head=head)
                if report is None:
                    from .finance import paise
                    record["partial_execution"]=dict(committed_fills=len(fills),estimated_fees_paise=sum(paise(f["fees"]) for f in fills),
                        last_committed_at=curve[-1]["time"] if curve else None,terminal_report_available=False,
                        note="Only committed simulation events; cancellation does not liquidate positions or imply a final valuation")
                attachments[item["id"]]=trace
            records.append(record)
        baseline = next((r for r in records if r["kind"]=="BASELINE" and r["report"]), None)
        repeat = next((r for r in records if r["kind"]=="REPRODUCE" and r["report"]), None)
        repeated=bool(baseline and repeat and len({r["report"]["economic_hash"] for r in records if r["report"] and r["kind"] in {"BASELINE","REPRODUCE"}})==1)
        reports = [r["report"] for r in records if r["report"]]
        findings = dict(
            data_quality=dict(status="UNVERIFIED", evidence=protocol["core"]["data_findings"], note="Source rights and authenticity are not independently verified"),
            reproducibility=dict(status="PASS" if repeated else "FAIL" if baseline and repeat else "UNVERIFIED", note="Identical pinned baseline/reproduction economic results", all_attempts_retained=len(records)),
            execution_realism=dict(status="UNVERIFIED", note="Observed top-of-book simulation; gross margin, no exchange queue or market-impact proof"),
            research_robustness=dict(status="UNVERIFIED", note="One fixed session is insufficient evidence of statistical edge; stress results are diagnostics"),
            operational_readiness=dict(status="PASS" if reports and len(reports)==len(records) and all(r["diagnostics"]["ledger_reconciled"] and r["diagnostics"]["attribution_reconciled"] and not r["positions"] and not r["pending_orders"] and not r["violations"] and r["diagnostics"]["fills"]>=2*len(protocol["core"]["definition"]["strategy"]["legs"]) for r in reports) else "FAIL", note="Accounting and flat-book checks only; no commercial or live approval"))
        # Summary exports retain aggregate results, never quote-bearing fill/position records.
        for item in records:
            if item["report"]:
                full=item["report"]
                item["report"]={key:full[key] for key in ("environment","config","config_hash","dataset_id","dataset_hash","economic_hash","performance","diagnostics")}
                item["report"]["result"]=dict(equity=full["account"]["equity"],
                    net_pnl=full["account"]["equity"]-full["config"]["capital"],
                    positions=len(full["positions"]),pending_orders=len(full["pending_orders"]))
        with self.workspace.lock, self.workspace.db:
            prior=self._request(request.request_id,"PACKET",payload)
            if prior:return self._get("mvp_packets",prior)
            # A concurrent new attempt cannot be silently omitted from this snapshot.
            if [t["id"] for t in self.trials(request.protocol_id)] != [t["id"] for t in trials]:
                raise ValueError("Trial inventory changed; capture a new evidence packet")
            core = dict(schema_version=VERSION, protocol=protocol, trials=records, findings=findings,
                        captured_at=datetime.now(timezone.utc).isoformat(), event_head=self.audit(),
                        source_authenticity_verified=False, live_orders_enabled=False,
                        export_scope="PRIVATE_RESEARCH_NO_RAW_DATASET")
            packet = dict(schema_version=VERSION, core=core, packet_hash=digest(core), attachments=attachments)
            if len(canonical(packet).encode())>MAX_PACKET_BYTES:raise ValueError("Evidence exceeds 16 MB; use a smaller scoped evaluation")
            if not verify_packet(packet,expected_protocol=request.protocol_id)["integrity_valid"]:raise ValueError("Execution evidence failed verification before capture")
            record = hashed_record(dict(protocol_id=request.protocol_id, packet=packet))
            self.workspace.db.execute("INSERT INTO mvp_packets VALUES(?,?,?)", (record["id"], request.protocol_id, canonical(record)))
            self._bind(request.request_id, "PACKET", payload, record["id"])
            self._event("PACKET_CAPTURED", record["id"], dict(protocol_id=request.protocol_id, packet_hash=packet["packet_hash"]))
        return record

    def export(self, identifier, include_trace=False, rights_acknowledged=False, allow_historical=False):
        record = self._get("mvp_packets", identifier)
        protocol_id = record["core"]["protocol_id"]
        packet = copy.deepcopy(record["core"]["packet"])
        current = [t["id"] for t in self.trials(protocol_id)]
        historical = current != [t["id"] for t in packet["core"]["trials"]] or packet["core"]["protocol"]["core"]["build"]!=CURRENT_BUILD
        if historical and not allow_historical:
            raise ValueError("Trial inventory or build changed; explicitly export this historical packet or capture a current one")
        if include_trace:
            protocol = packet["core"]["protocol"]["core"]
            definition = protocol["definition"]
            source = protocol["dataset_snapshot"]["source_kind"]
            allowed = source=="SYNTHETIC" or (definition["data"]["rights_status"]=="OWNER_ASSERTED" and bool(definition["data"]["rights_reference"].strip()))
            if not rights_acknowledged or not allowed:
                raise ValueError("Quote-bearing trace export requires an explicit permitted-private-use acknowledgment and source-rights reference")
        else:
            packet["attachments"] = {}
        packet["historical"] = historical
        packet["trace_included"] = include_trace
        if not verify_packet(packet)["integrity_valid"]:
            raise ValueError("Evidence packet verification failed")
        return packet

    def packet_summary(self,record):
        packet=record["core"]["packet"]
        ids=[t["id"] for t in self.trials(record["core"]["protocol_id"])]
        return dict(id=record["id"],created_at=record["created_at"],protocol_id=record["core"]["protocol_id"],
            packet_hash=packet["packet_hash"],findings=packet["core"]["findings"],
            historical=ids!=[t["id"] for t in packet["core"]["trials"]] or packet["core"]["protocol"]["core"]["build"]!=CURRENT_BUILD)

    def load_example(self,request_id):
        from .research.fixtures import contract_fixture
        definition,body=contract_fixture()
        dataset=self.research.catalog.import_data(body)
        saved=self.book.save(dict(request_id="example-"+request_id,definition=definition,dataset_id=dataset["id"]))
        return dict(contract=saved,dataset_id=dataset["id"],controlled_example=True,live_orders_enabled=False)

    def packets(self, protocol_id):
        self.get_protocol(protocol_id)
        with self.workspace.lock:
            ids=[r[0] for r in self.workspace.db.execute("SELECT id FROM mvp_packets WHERE protocol_id=? ORDER BY rowid DESC LIMIT 100",(protocol_id,))]
        return [self.packet_summary(self._get("mvp_packets",i)) for i in ids]


def router(workspace):
    service = ContractResearch(workspace)
    api = APIRouter(prefix="/api/contract-research", tags=["Frozen contract research — paper only"])

    @api.get("/catalog")
    def catalog():
        return dict(schema_version=VERSION,risk_defaults=Risk().model_dump(mode="json"),capital_paise=1000000000,
            max_packet_bytes=MAX_PACKET_BYTES,live_orders_enabled=False,scenarios=["BASELINE","REPRODUCE","COST_STRESS"])

    @api.post("/example")
    def example(body:ExampleRequest):
        return service.load_example(body.request_id)

    @api.get("/packets/{identifier}")
    def packet(identifier:Hash):
        return service.export(identifier,allow_historical=True)

    @api.get("/protocols")
    def protocols():
        return service.protocols()

    @api.post("/protocols")
    def freeze(body: Freeze):
        return service.freeze(body)

    @api.get("/protocols/{identifier}")
    def protocol(identifier: Hash):
        return dict(protocol=service.get_protocol(identifier),trials=service.trials(identifier),packets=service.packets(identifier),audit=service.audit(),
                    current_build=service.get_protocol(identifier)["core"]["build"]==CURRENT_BUILD)

    @api.post("/trials")
    def start(body: Start):
        return service.start(body)

    @api.post("/trials/{identifier}/recover")
    def recover(identifier: Hash):
        return service.dispatch(identifier)

    @api.post("/trials/{identifier}/control")
    def control(identifier: Hash, body: TrialControl):
        return service.control(identifier, body.action)

    @api.post("/packets")
    def capture(body: Capture):
        return service.packet_summary(service.capture(body))

    @api.get("/packets/{identifier}/export")
    def export(identifier: Hash, include_trace: bool=False, rights_acknowledged: bool=False, allow_historical: bool=False):
        packet=service.export(identifier,include_trace,rights_acknowledged,allow_historical)
        return Response(canonical(packet),media_type="application/json",headers={"Content-Disposition":"attachment; filename=contract-evidence-"+identifier+".json"})

    @api.post("/verify")
    def verify(body: PacketInput):
        return verify_packet(body.packet,body.expected_protocol,body.expected_build,body.expected_dataset)

    return api
