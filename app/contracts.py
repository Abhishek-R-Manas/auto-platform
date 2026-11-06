"""MVP-1 definitions and data compatibility; no research job or order dispatch."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
import json
from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import Field, StrictBool, StrictInt, field_validator

from .research.catalog import canonical, digest, session_for
from .research.schemas import Strict, ID, aware

Text = Annotated[str, Field(min_length=1, max_length=200)]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Money = Annotated[StrictInt, Field(ge=0, le=1_000_000_000)]


class Customer(Strict):
    team_role: Literal["OPTIONS_RESEARCH", "RA_ALGO_PROVIDER", "OPTIONS_DESK"]
    research_question: Annotated[str, Field(min_length=10, max_length=2000)]
    budget_owner: Annotated[str, Field(max_length=200)] = ""
    validation_state: Literal["PROVISIONAL", "REVIEWED"] = "PROVISIONAL"
    evidence_reference: Annotated[str, Field(max_length=1000)] = ""
    baseline_review_minutes: Annotated[StrictInt, Field(ge=1, le=100000)] | None = None


class ContractLeg(Strict):
    instrument_id: ID
    kind: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike_paise: Annotated[StrictInt, Field(gt=0, le=1_000_000_000)]
    premium_paise: Annotated[StrictInt, Field(gt=0, le=1_000_000_000)]
    expiry: datetime
    _expiry = field_validator("expiry")(aware)


class Terms(Strict):
    underlying: Literal["NIFTY"]
    reference_id: ID
    structure: Literal["CALL_VERTICAL", "PUT_VERTICAL", "IRON_CONDOR"]
    lots: Annotated[StrictInt, Field(ge=1, le=1000)]
    lot_size: Annotated[StrictInt, Field(ge=1, le=100000)]
    tick_size_paise: Annotated[StrictInt, Field(ge=1, le=100000)]
    terms_effective_at: datetime
    legs: Annotated[list[ContractLeg], Field(min_length=2, max_length=4)]
    _effective = field_validator("terms_effective_at")(aware)


class Rules(Strict):
    entry_at: datetime
    exit_at: datetime
    selection: Literal["FIXED_CONTRACTS"]
    reentry: Literal["NONE"]
    adjustments: Literal["NONE"]
    allow_overnight: Literal[False]
    partial_fill_policy: Literal["HEDGES_FIRST_UNWIND_ON_FAILURE"]
    session_open: time = time(9, 15)
    session_close: time = time(15, 30)
    _dates = field_validator("entry_at", "exit_at")(aware)


class Risk(Strict):
    max_expiry_loss_paise: Annotated[StrictInt, Field(gt=0, le=1_000_000_000_000)]
    estimated_costs_paise: Money


class DataRequirements(Strict):
    required_fidelity: Literal["QUOTE", "MINUTE"]
    max_quote_age_seconds: Annotated[StrictInt, Field(ge=1, le=900)]
    min_depth_lots: Annotated[StrictInt, Field(ge=1, le=1000)]
    max_spread_bps: Annotated[StrictInt, Field(ge=1, le=10000)]
    provider: Literal["UNSELECTED", "KITE_PERSONAL", "LICENSED_VENDOR"]
    intended_use: Literal["LOCAL_RESEARCH", "CLIENT_RESEARCH", "REDISTRIBUTION"]
    source_note: Annotated[str, Field(max_length=1000)] = ""
    rights_reference: Annotated[str, Field(max_length=1000)] = ""
    rights_status: Literal["UNKNOWN", "OWNER_ASSERTED"] = "UNKNOWN"
    calendar_reviewed: StrictBool = False
    calendar_reference: Annotated[str, Field(max_length=1000)] = ""
    fees_reviewed: StrictBool = False
    fees_reference: Annotated[str, Field(max_length=1000)] = ""
    receipt_time_status: Literal["NOT_VERIFIED", "DOCUMENTED"] = "NOT_VERIFIED"
    receipt_time_reference: Annotated[str, Field(max_length=1000)] = ""


class Definition(Strict):
    schema_version: Literal["mvp1/1.0"]
    name: Text
    customer: Customer
    strategy: Terms
    rules: Rules
    risk: Risk
    data: DataRequirements


class Check(Strict):
    definition: Definition
    dataset_id: Hash | None = None


class Save(Check):
    request_id: Annotated[str, Field(min_length=8, max_length=100)]
    parent_id: Hash | None = None


def expiry_payoff(legs, units, costs):
    """Integer-paise common-expiry bound for the supported fully hedged structures."""
    values=[]
    for spot in [0,*sorted({l["strike_paise"] for l in legs})]:
        value=-costs
        for leg in legs:
            intrinsic=max(0,spot-leg["strike_paise"]) if leg["kind"]=="CE" else max(0,leg["strike_paise"]-spot)
            value+=units*(1 if leg["side"]=="BUY" else -1)*(intrinsic-leg["premium_paise"])
        values.append(value)
    return dict(max_loss_paise=max(0,-min(values)),max_profit_paise=max(0,max(values)),values=values)


def assess(definition, dataset=None, rows=None):
    """Assess declared semantics and observed compatibility without conferring authority."""
    d = Definition.model_validate(definition).model_dump(mode="json")
    s, r, data = d["strategy"], d["rules"], d["data"]
    legs = s["legs"]
    findings = []

    def add(code, category, status, message):
        findings.append(dict(code=code, category=category, status=status, message=message))

    def check(code, passed, message, category="DEFINITION"):
        add(code, category, "PASS" if passed else "BLOCKED", message)

    entry, exit_at = aware(r["entry_at"]), aware(r["exit_at"])
    local_entry, local_exit = entry.astimezone(ZoneInfo("Asia/Kolkata")), exit_at.astimezone(ZoneInfo("Asia/Kolkata"))
    same_day = local_entry.date() == local_exit.date()
    check("TIME_ORDER", entry < exit_at, "Exit must follow the one scheduled entry.")
    check("SAME_SESSION", same_day, "Entry and exit must belong to the same IST date; overnight and reentry are unsupported.")
    open_at, close_at = time.fromisoformat(r["session_open"]), time.fromisoformat(r["session_close"])
    check("SESSION", open_at.tzinfo is None and close_at.tzinfo is None and open_at < close_at and open_at <= local_entry.time() < local_exit.time() < close_at,
          "Scheduled times must be inside the declared local session; the calendar requires separate review.")
    check("TERMS_CUTOFF", aware(s["terms_effective_at"]) <= entry, "Contract terms must be effective by entry.")
    check("EXPIRY", len({l["expiry"] for l in legs}) == 1 and all(exit_at < aware(l["expiry"]) for l in legs),
          "All legs must share one exact expiry after the planned exit.")
    check("INSTRUMENT_ID", len({l["instrument_id"] for l in legs}) == len(legs) and s["reference_id"] not in {l["instrument_id"] for l in legs},
          "Each leg and the underlying reference require distinct stable identities.")
    check("TICK", all(l["premium_paise"] % s["tick_size_paise"] == 0 for l in legs), "Illustrative entry premiums must align with the declared tick.")
    check("SIZE", s["lot_size"] * s["lots"] <= 1_000_000, "This first contract supports up to one million units per leg.")
    shape = False
    if s["structure"] in {"CALL_VERTICAL", "PUT_VERTICAL"}:
        kind = "CE" if s["structure"] == "CALL_VERTICAL" else "PE"
        shape = len(legs) == 2 and all(l["kind"] == kind for l in legs) and {l["side"] for l in legs} == {"BUY", "SELL"} and len({l["strike_paise"] for l in legs}) == 2
    else:
        indexed = {(l["kind"], l["side"]): l["strike_paise"] for l in legs}
        shape = len(legs) == len(indexed) == 4 and set(indexed) == {("CE", "BUY"), ("CE", "SELL"), ("PE", "BUY"), ("PE", "SELL")}
        if shape:
            shape = indexed[("PE", "BUY")] < indexed[("PE", "SELL")] < indexed[("CE", "SELL")] < indexed[("CE", "BUY")]
    check("STRUCTURE", shape, "Use an equal-unit call/put vertical or a non-crossed, wing-hedged iron condor.")
    units = s["lot_size"] * s["lots"]
    payoff = None
    if shape and units <= 1_000_000 and len({l["expiry"] for l in legs}) == 1:
        bound=expiry_payoff(legs,units,d["risk"]["estimated_costs_paise"])
        values=bound.pop("values")
        payoff=dict(**bound,units_per_leg=units,basis="Declared premiums, complete legs and estimated costs at common expiry; execution and margin remain unverified.")
        check("LOSS_BUDGET", payoff["max_loss_paise"] <= d["risk"]["max_expiry_loss_paise"],
              "Contractual expiry loss plus declared estimated costs must fit the research loss budget.")
        check("PREMIUM_BOUNDS", min(values) <= 0 <= max(values), "Declared premiums must not imply a guaranteed profit or loss across every terminal price.")
    customer = d["customer"]
    customer_validated = customer["validation_state"] == "REVIEWED" and bool(customer["evidence_reference"].strip() and customer["budget_owner"].strip())
    add("CUSTOMER_EVIDENCE", "CUSTOMER", "PASS" if customer_validated else "UNVERIFIED",
        "Customer review is an owner assertion requiring a budget owner and actual evidence reference; no interview is inferred.")
    add("CALENDAR_REVIEW", "DATA", "PASS" if data["calendar_reviewed"] and data["calendar_reference"].strip() else "UNVERIFIED", "Supply a reviewed calendar reference, including holidays and special sessions.")
    add("FEES_REVIEW", "DATA", "PASS" if data["fees_reviewed"] and data["fees_reference"].strip() else "UNVERIFIED", "Supply the dated fee schedule review reference.")
    add("RECEIPT_TIMES", "DATA", "UNVERIFIED", "The existing research rows do not independently establish exchange and first-receipt timestamps; a source contract and adapter review are required.")
    add("SOURCE_SELECTION", "DATA", "PASS" if data["provider"] != "UNSELECTED" and data["source_note"].strip() else "UNVERIFIED", "Select and describe the proposed source; selection alone is not acceptance.")
    add("DATA_RIGHTS", "COMMERCIAL", "UNVERIFIED", "Rights and references are owner declarations, not independently verified commercial data rights.")
    if data["provider"] == "KITE_PERSONAL" and data["intended_use"] != "LOCAL_RESEARCH":
        add("KITE_DISTRIBUTION", "COMMERCIAL", "BLOCKED", "Personal Kite data cannot be treated as an authorized customer redistribution feed.")
    snapshot = None
    if dataset is None:
        add("DATASET_REQUIRED", "DATA", "BLOCKED", "Select an immutable dataset to inspect actual coverage and contract terms.")
    else:
        m = dataset["manifest"]
        snapshot = {key: dataset[key] for key in ("id", "raw_hash", "parquet_hash", "status")}
        snapshot.update(source=m["source"], source_kind=m["source_kind"], frequency=m["frequency"])
        check("DATASET_STATUS", dataset["status"] == "READY", "Dataset must pass ingestion and integrity checks.", "DATA")
        check("DATA_FIDELITY", m["frequency"] == data["required_fidelity"], "Dataset frequency must match the declared fidelity; candles cannot establish historical quotes.", "DATA")
        check("RAW_PRICES", not m["prices_adjusted"], "Executable-price research requires unadjusted contract observations.", "DATA")
        indexed = {i["id"]: i for i in m["instruments"]}
        reference = indexed.get(s["reference_id"])
        terms_ok = bool(reference and reference["kind"] == "INDEX" and reference["symbol"].replace(" ", "").upper() in {"NIFTY", "NIFTY50"})
        for l in legs:
            i = indexed.get(l["instrument_id"])
            if not i:
                terms_ok = False
                continue
            applicable = [t for t in i["terms"] if aware(t["effective_at"]) <= entry]
            terms = applicable[-1] if applicable else i
            terms_ok = terms_ok and i["kind"] == "OPT" and i["exchange"] == "NFO" and i["underlying"] == s["reference_id"] and i["option_type"] == l["kind"] and aware(i["expiry"]) == aware(l["expiry"]) and Decimal(str(i["strike"]))*100 == l["strike_paise"] and terms["lot_size"] == s["lot_size"] and Decimal(str(terms["tick_size"]))*100 == s["tick_size_paise"] and (not i["listed_at"] or aware(i["listed_at"]) <= entry) and (not i["delisted_at"] or exit_at < aware(i["delisted_at"]))
        check("DATA_CONTRACT_TERMS", bool(terms_ok), "Observed instrument identity, underlying, option type, strike, expiry and effective lot/tick terms must match.", "DATA")
        day = local_entry.date()
        session = session_for(m["calendar"], day)
        check("DATA_CALENDAR", bool(session and m["calendar"]["timezone"] == "Asia/Kolkata" and time.fromisoformat(session["open"]) <= local_entry.time() < local_exit.time() < time.fromisoformat(session["close"])), "The dataset calendar must cover both scheduled decisions.", "DATA")
        check("DATED_FEES", any(aware(f["effective_from"]) <= entry for f in m["fee_rules"]), "At least one declared fee schedule must be effective by entry.", "DATA")
        required = {s["reference_id"], *(l["instrument_id"] for l in legs)}
        observations = {key: [] for key in required}
        for row in rows or []:
            if row["instrument_id"] in observations:
                observations[row["instrument_id"]].append(row)
        coverage, liquidity = True, True
        for key, records in observations.items():
            records.sort(key=lambda row: row["timestamp"])
            for cutoff in (entry, exit_at):
                eligible = [row for row in records if aware(row["timestamp"]) <= cutoff]
                last = eligible[-1] if eligible else None
                fresh = bool(last and (cutoff-aware(last["timestamp"])).total_seconds() <= data["max_quote_age_seconds"])
                coverage = coverage and fresh
                if key == s["reference_id"] or data["required_fidelity"] != "QUOTE":
                    continue
                if not fresh or not last["bid"] or not last["ask"]:
                    liquidity = False
                    continue
                spread = (last["ask"]-last["bid"])/((last["ask"]+last["bid"])/2)*10000
                liquidity = liquidity and min(last["bid_quantity"] or 0, last["ask_quantity"] or 0) >= s["lot_size"]*max(s["lots"], data["min_depth_lots"]) and spread <= data["max_spread_bps"]
        check("QUOTE_COVERAGE", bool(coverage), "Each instrument needs a fresh observation at or before both boundaries; these checks do not establish continuous coverage.", "DATA")
        if data["required_fidelity"] == "QUOTE":
            check("QUOTE_LIQUIDITY", bool(liquidity), "Both sides require the declared spread/depth at entry and exit boundaries; displayed depth is not a fill guarantee.", "DATA")
        if m["source_kind"] == "SYNTHETIC":
            add("SYNTHETIC_DATA", "COMMERCIAL", "BLOCKED", "Synthetic inputs can exercise software only and do not establish genuine market or customer evidence.")
        if data["required_fidelity"] == "MINUTE":
            add("BAR_ASSUMPTIONS", "DATA", "UNVERIFIED", "Intrabar ordering, spreads and liquidity remain assumptions; minute candles do not establish executable quotes.")
    return dict(schema_version="mvp1/1.0", definition_hash=digest(d),
                definition_ready=not any(f["category"] == "DEFINITION" and f["status"] == "BLOCKED" for f in findings),
                data_ready=not any(f["category"] == "DATA" and f["status"] != "PASS" for f in findings),
                customer_validated=customer_validated, commercial_data_ready=False, rights_independently_verified=False,
                findings=findings, payoff=payoff, dataset_snapshot=snapshot,
                intent=dict(entry_at=r["entry_at"], exit_at=r["exit_at"], selection=r["selection"], reentry=r["reentry"],
                            adjustments=r["adjustments"], allow_overnight=False, partial_fill_policy=r["partial_fill_policy"],
                            contracts=[dict(instrument_id=l["instrument_id"], signed_units=units*(1 if l["side"] == "BUY" else -1)) for l in legs],
                            semantics="One scheduled fixed-contract basket; hedges before shorts and unwind on failure are requirements for the next-stage execution adapter."),
                execution_implemented=False, live_orders_enabled=False, backtest_submitted=False, forward_created=False)


def verify_record(item):
    if digest(item["core"]) != item["id"]:
        raise ValueError("Strategy contract integrity failed")
    Definition.model_validate(item["core"]["definition"])
    return item


def request_matches(item, expected_hash):
    core = item["core"]
    actual = digest({key: core[key] for key in ("definition", "dataset_id", "parent_id")})
    if actual != expected_hash:
        raise ValueError("Strategy contract request integrity failed")
    return item


class ContractBook:
    def __init__(self, workspace):
        self.workspace = workspace
        self.catalog = workspace.research.catalog
        with workspace.lock, workspace.db:
            workspace.db.execute("CREATE TABLE IF NOT EXISTS mvp_contracts(id TEXT PRIMARY KEY, document TEXT NOT NULL)")
            workspace.db.execute("CREATE TABLE IF NOT EXISTS mvp_contract_requests(request_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, contract_id TEXT NOT NULL)")

    def check(self, definition, dataset_id=None):
        definition = Definition.model_validate(definition)
        dataset = self.catalog.verify(dataset_id) if dataset_id else None
        rows = self.catalog.rows(dataset_id) if dataset else None
        return assess(definition, dataset, rows)

    def get(self, identifier):
        with self.workspace.lock:
            row = self.workspace.db.execute("SELECT document FROM mvp_contracts WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("Strategy contract not found")
        item = verify_record(json.loads(row[0]))
        if item["id"] != identifier:
            raise ValueError("Strategy contract identity integrity failed")
        return item

    def list(self):
        with self.workspace.lock:
            rows = self.workspace.db.execute("SELECT id FROM mvp_contracts ORDER BY rowid DESC LIMIT 200").fetchall()
            return [dict(id=(item := self.get(row[0]))["id"], name=item["core"]["definition"]["name"],
                         parent_id=item["core"]["parent_id"], created_at=item["created_at"],
                         definition_ready=item["core"]["assessment"]["definition_ready"],
                         data_ready=item["core"]["assessment"]["data_ready"]) for row in rows]

    def save(self, body):
        request = Save.model_validate(body)
        normalized = request.model_dump(mode="json", exclude={"request_id"})
        input_hash = digest(normalized)
        with self.workspace.lock:
            existing = self.workspace.db.execute("SELECT input_hash, contract_id FROM mvp_contract_requests WHERE request_id=?", (request.request_id,)).fetchone()
            if existing:
                if existing[0] != input_hash:
                    raise ValueError("Request id already binds different contract inputs")
                return request_matches(self.get(existing[1]), input_hash)
        report = self.check(request.definition, request.dataset_id)
        core = dict(definition=request.definition.model_dump(mode="json"), dataset_id=request.dataset_id,
                    parent_id=request.parent_id, assessment=report)
        identifier = digest(core)
        with self.workspace.lock, self.workspace.db:
            existing = self.workspace.db.execute("SELECT input_hash, contract_id FROM mvp_contract_requests WHERE request_id=?", (request.request_id,)).fetchone()
            if existing:
                if existing[0] != input_hash:
                    raise ValueError("Request id already binds different contract inputs")
                return request_matches(self.get(existing[1]), input_hash)
            if request.parent_id:
                self.get(request.parent_id)
            if self.workspace.db.execute("SELECT count(*) FROM mvp_contracts").fetchone()[0] >= 2000:
                raise ValueError("Review contract storage capacity before adding more revisions")
            row = self.workspace.db.execute("SELECT id FROM mvp_contracts WHERE id=?", (identifier,)).fetchone()
            if not row:
                item = dict(id=identifier, core=core, created_at=datetime.now(timezone.utc).isoformat())
                self.workspace.db.execute("INSERT INTO mvp_contracts VALUES(?,?)", (identifier, canonical(item)))
            self.workspace.db.execute("INSERT INTO mvp_contract_requests VALUES(?,?,?)", (request.request_id, input_hash, identifier))
            return self.get(identifier)


def example(structure="CALL_VERTICAL"):
    """Explicitly fictional input; dated lot size and premiums are never market claims."""
    legs = [
        dict(instrument_id="C25000", kind="CE", side="BUY", strike_paise=2500000, premium_paise=10000, expiry="2026-11-12T15:30:00+05:30"),
        dict(instrument_id="C25100", kind="CE", side="SELL", strike_paise=2510000, premium_paise=6000, expiry="2026-11-12T15:30:00+05:30")]
    if structure == "PUT_VERTICAL":
        legs = [dict(l, instrument_id="P"+str(l["strike_paise"]//100), kind="PE", side="SELL" if n == 0 else "BUY") for n, l in enumerate(legs)]
        legs[0]["premium_paise"], legs[1]["premium_paise"] = 6000, 10000
    if structure == "IRON_CONDOR":
        legs = [dict(instrument_id=key, kind=kind, side=side, strike_paise=strike*100, premium_paise=premium, expiry="2026-11-12T15:30:00+05:30")
                for key, kind, side, strike, premium in [("P24800", "PE", "BUY", 24800, 2000), ("P24900", "PE", "SELL", 24900, 4000), ("C25100", "CE", "SELL", 25100, 4000), ("C25200", "CE", "BUY", 25200, 2000)]]
    return Definition.model_validate(dict(schema_version="mvp1/1.0", name="Illustrative "+structure.replace("_", " ").lower(),
        customer=dict(team_role="OPTIONS_RESEARCH", research_question="Does this explicitly specified spread remain useful after realistic costs?"),
        strategy=dict(underlying="NIFTY", reference_id="NIFTY", structure=structure, lots=1, lot_size=65, tick_size_paise=5, terms_effective_at="2026-11-01T00:00:00+05:30", legs=legs),
        rules=dict(entry_at="2026-11-06T09:30:00+05:30", exit_at="2026-11-06T15:00:00+05:30", selection="FIXED_CONTRACTS", reentry="NONE", adjustments="NONE", allow_overnight=False, partial_fill_policy="HEDGES_FIRST_UNWIND_ON_FAILURE"),
        risk=dict(max_expiry_loss_paise=500000, estimated_costs_paise=4000),
        data=dict(required_fidelity="QUOTE", max_quote_age_seconds=30, min_depth_lots=1, max_spread_bps=500, provider="UNSELECTED", intended_use="LOCAL_RESEARCH"))).model_dump(mode="json")


def router(workspace):
    book = ContractBook(workspace)
    api = APIRouter(prefix="/api/contracts", tags=["Strategy contracts — definitions only"])

    @api.get("/catalog")
    def catalog():
        return dict(schema_version="mvp1/1.0", structures=["CALL_VERTICAL", "PUT_VERTICAL", "IRON_CONDOR"],
                    supported_rules=["One scheduled entry", "Same-session scheduled exit", "Fixed contracts", "Equal units", "No adjustments or reentry"],
                    examples={s: example(s) for s in ("CALL_VERTICAL", "PUT_VERTICAL", "IRON_CONDOR")},
                    live_orders_enabled=False, execution_implemented=False)

    @api.get("/schema")
    def schema():
        return Definition.model_json_schema()

    @api.post("/check")
    def check_contract(body: Check):
        return book.check(body.definition, body.dataset_id)

    @api.get("")
    def contracts():
        return book.list()

    @api.post("")
    def save_contract(body: Save):
        return book.save(body)

    @api.get("/{identifier}/export")
    def export(identifier: Hash):
        return Response(canonical(book.get(identifier)), media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=strategy-contract-"+identifier+".json"})

    @api.get("/{identifier}")
    def contract(identifier: Hash):
        return book.get(identifier)

    return api
