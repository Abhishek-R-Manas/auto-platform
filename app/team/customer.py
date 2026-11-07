"""MVP-5 organization-owned customer journeys and commercial evidence.

This is an operating/evidence journal. Existing research engines and execution
permissions remain authoritative; a journal entry never places a paper or broker order.
"""
from datetime import datetime, timezone
import json
from statistics import median
from typing import Annotated, Literal
import uuid

from fastapi import HTTPException
from pydantic import Field, StrictInt, StringConstraints, field_validator, model_validator

from ..contracts import Hash
from ..research.catalog import aware, canonical, digest
from ..research.jobs import CURRENT_BUILD
from ..research.schemas import Strict

VERSION = "mvp5/1.0"
RequestID = Annotated[str, Field(min_length=8, max_length=100)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Amount = Annotated[StrictInt, Field(gt=0, le=1_000_000_000_000)]
Minutes = Annotated[StrictInt, Field(ge=1, le=100000)]


class Engagement(Strict):
    request_id: RequestID
    name: Name
    evidence_kind: Literal["CONTROLLED_EXERCISE", "CUSTOMER_REPORTED"]
    workflow_reference: Text
    baseline_review_seconds: Annotated[StrictInt, Field(gt=0, le=10_000_000)]
    baseline_reference: Text


class Cycle(Strict):
    request_id: RequestID
    engagement_id: Hash
    packet_id: Hash
    question: Text


class Timer(Strict):
    request_id: RequestID
    cycle_id: Hash
    action: Literal["START", "FINISH", "ABANDON"]
    review_id: Hash | None = None


class Plan(Strict):
    request_id: RequestID
    cycle_id: Hash
    review_id: Hash
    paper_book_reference: Text


class Observation(Strict):
    request_id: RequestID
    plan_id: Hash
    observed_at: datetime
    kind: Literal["ON_TRACK", "DATA_GAP", "EXECUTION_DIFFERENCE", "MATERIAL_CHANGE", "ENDED"]
    note: Text
    evidence_reference: Text
    reported_pnl_paise: Annotated[StrictInt, Field(ge=-1_000_000_000_000, le=1_000_000_000_000)] | None = None
    _aware = field_validator("observed_at")(aware)


class CorrectionReview(Strict):
    request_id: RequestID
    observation_id: Hash
    decision: Literal["CONFIRMED", "REJECTED"]
    note: Text
    evidence_reference: Text


class Effort(Strict):
    request_id: RequestID
    engagement_id: Hash
    kind: Literal["ONBOARDING", "SUPPORT", "REVIEW"]
    minutes: Minutes
    evidence_reference: Text


class Finance(Strict):
    request_id: RequestID
    engagement_id: Hash
    kind: Literal["INVOICE", "PAYMENT", "REFUND", "COST", "VOID_INVOICE", "REVERSE_ENTRY"]
    amount_paise: Amount | None = None
    reference: Name
    evidence_reference: Text
    occurred_at: datetime
    invoice_id: Hash | None = None
    target_record_id: Hash | None = None
    renews_invoice_id: Hash | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    cost_category: Literal["DATA", "MODEL", "COMPUTE", "REVIEW", "SUPPORT", "OTHER"] | None = None
    _aware = field_validator("occurred_at", "period_start", "period_end")(lambda v: aware(v) if v else v)

    @model_validator(mode="after")
    def fields(self):
        if self.kind == "REVERSE_ENTRY":
            if not self.target_record_id or self.amount_paise is not None or self.invoice_id:
                raise ValueError("Entry correction requires a target record and no money movement")
        elif self.target_record_id:
            raise ValueError("Only entry corrections identify a target record")
        elif self.kind == "VOID_INVOICE":
            if not self.invoice_id or self.amount_paise is not None:
                raise ValueError("Voiding requires an invoice and no amount")
        elif self.amount_paise is None:
            raise ValueError("Supply a positive amount in paise")
        if self.kind == "INVOICE":
            if self.invoice_id or not self.period_start or not self.period_end or self.period_start >= self.period_end:
                raise ValueError("Invoice requires its own nonempty period")
        elif self.period_start or self.period_end or self.renews_invoice_id:
            raise ValueError("Only invoices declare a period or renewal")
        if self.kind in {"PAYMENT", "REFUND"} and not self.invoice_id:
            raise ValueError("Select the related invoice")
        if self.kind == "COST" and (not self.cost_category or self.invoice_id):
            raise ValueError("Costs require a category and no invoice")
        if self.kind != "COST" and self.cost_category:
            raise ValueError("Only delivery costs have a cost category")
        return self


class Demand(Strict):
    request_id: RequestID
    engagement_id: Hash
    integration: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    problem: Text
    evidence_reference: Text


def check_customer_database(db):
    previous = "0"*64
    records = {}
    for identifier, kind, parent, raw in db.execute("SELECT id,kind,parent_id,document FROM team_customer_records ORDER BY rowid"):
        item = json.loads(raw)
        core = item["core"]
        if digest(core) != identifier or item["id"] != identifier or core["kind"] != kind or core["parent_id"] != parent:
            raise ValueError("Customer record integrity failed")
        if parent and parent not in records:
            raise ValueError("Customer record parent is missing")
        records[identifier] = item
    seen = []
    for sequence, raw in db.execute("SELECT sequence,document FROM team_customer_events ORDER BY sequence"):
        event = json.loads(raw)
        expected = event.pop("hash")
        if sequence != len(seen)+1 or event["sequence"] != sequence or event["previous_hash"] != previous or digest(event) != expected:
            raise ValueError("Customer event chain integrity failed")
        if event["record_id"] not in records or event["record_id"] in seen:
            raise ValueError("Customer event inventory integrity failed")
        seen.append(event["record_id"])
        previous = expected
    if set(seen) != set(records):
        raise ValueError("Customer records and events differ")
    requests = 0
    for expected, identifier in db.execute("SELECT input_hash,record_id FROM team_customer_requests"):
        if identifier not in records or digest(records[identifier]["core"]["inputs"]) != expected:
            raise ValueError("Customer request integrity failed")
        requests += 1
    if requests != len(records):
        raise ValueError("Customer request inventory is incomplete")
    return dict(records_verified=len(records), events_verified=len(seen), head=previous)


class CustomerOperations:
    def __init__(self, team):
        self.team = team
        self.db = team.workspace.db
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS team_customer_records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,parent_id TEXT NOT NULL,document TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS team_customer_parent ON team_customer_records(kind,parent_id);
          CREATE TABLE IF NOT EXISTS team_customer_requests(request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,record_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS team_customer_events(sequence INTEGER PRIMARY KEY,document TEXT NOT NULL);
        """)

    def now(self):
        return datetime.fromtimestamp(self.team.identity.clock(), timezone.utc)

    def _enter(self, actor, *roles):
        self.team.identity.require(actor, *roles)
        check_customer_database(self.db)

    def _get(self, identifier, kind=None):
        row = self.db.execute("SELECT document FROM team_customer_records WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, "Customer record not found in this organization")
        item = json.loads(row[0])
        if item["core"]["organization_id"] != self.team.identity.organization()["id"]:
            raise HTTPException(404, "Customer record not found in this organization")
        if kind and item["core"]["kind"] != kind:
            raise ValueError("Select the required customer record type")
        return item

    def records(self, kind=None, parent=None):
        query = "SELECT id FROM team_customer_records WHERE 1=1"
        arguments = []
        if kind:
            query += " AND kind=?"; arguments.append(kind)
        if parent:
            query += " AND parent_id=?"; arguments.append(parent)
        return [self._get(row[0]) for row in self.db.execute(query+" ORDER BY rowid", arguments)]

    def _prior(self, actor, kind, body):
        key = self.team.request_key(actor, "customer:"+body["request_id"])
        inputs = dict(actor_id=actor["id"], kind=kind, body={k:v for k,v in body.items() if k != "request_id"})
        row = self.db.execute("SELECT input_hash,record_id FROM team_customer_requests WHERE request_id=?", (key,)).fetchone()
        if row:
            if row[0] != digest(inputs):
                raise ValueError("Request ID already binds different customer inputs")
            return self._get(row[1])

    def _add(self, actor, kind, parent, body, extra):
        if self.db.execute("SELECT count(*) FROM team_customer_records").fetchone()[0] >= 10000:
            raise ValueError("Customer evidence capacity reached; review retention before adding records")
        inputs = dict(actor_id=actor["id"], kind=kind, body={k:v for k,v in body.items() if k != "request_id"})
        core = dict(kind=kind, parent_id=parent, inputs=inputs, organization_id=self.team.identity.organization()["id"],
            authenticated_context=self.team.context(actor), recorded_at=self.now().isoformat(), nonce=uuid.uuid4().hex, **extra)
        item = dict(id=digest(core), core=core)
        last = self.db.execute("SELECT document FROM team_customer_events ORDER BY sequence DESC LIMIT 1").fetchone()
        old = json.loads(last[0]) if last else None
        event = dict(sequence=old["sequence"]+1 if old else 1, previous_hash=old["hash"] if old else "0"*64, record_id=item["id"])
        event["hash"] = digest(event)
        with self.db:
            self.db.execute("INSERT INTO team_customer_records VALUES(?,?,?,?)", (item["id"],kind,parent,canonical(item)))
            self.db.execute("INSERT INTO team_customer_requests VALUES(?,?,?)", (self.team.request_key(actor,"customer:"+body["request_id"]),digest(inputs),item["id"]))
            self.db.execute("INSERT INTO team_customer_events VALUES(?,?)", (event["sequence"],canonical(event)))
        return item

    def create_engagement(self, actor, payload):
        body = Engagement.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor,"ADMIN")
            prior = self._prior(actor,"ENGAGEMENT",body)
            if prior:return prior
            if len(self.records("ENGAGEMENT")) >= 200:raise ValueError("Pilot engagement capacity reached")
            return self._add(actor,"ENGAGEMENT","",body,{k:v for k,v in body.items() if k != "request_id"})

    def create_cycle(self, actor, payload):
        body = Cycle.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor,"RESEARCHER")
            prior = self._prior(actor,"CYCLE",body)
            if prior:return prior
            engagement = self._get(body["engagement_id"],"ENGAGEMENT")
            packet = self.team.packet(body["packet_id"])
            p = self.team.protocol(packet["core"]["protocol_id"])
            if self.team.experiments.packet_summary(packet)["historical"]:
                raise ValueError("Select a current evidence packet for a new customer cycle")
            if any(c["core"]["packet_id"] == body["packet_id"] for c in self.records("CYCLE")):
                raise ValueError("This packet already belongs to a customer cycle; retain each actual journey once")
            return self._add(actor,"CYCLE",engagement["id"],body,dict(packet_id=body["packet_id"],protocol_id=p["id"],
                question=body["question"], evidence_kind=engagement["core"]["evidence_kind"],
                contributors=self.team._contributors(packet),
                baseline_review_seconds=engagement["core"]["baseline_review_seconds"],
                bindings=dict(packet_hash=packet["core"]["packet"]["packet_hash"],definition_hash=p["core"]["definition_hash"],
                    dataset_id=p["core"]["dataset_snapshot"]["id"],build=p["core"]["build"],risk_policy_hash=p["core"]["risk_policy_hash"])))

    def timer(self, actor, payload):
        body = Timer.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor,"REVIEWER")
            prior = self._prior(actor,"TIMER_"+body["action"],body)
            if prior:return prior
            cycle = self._get(body["cycle_id"],"CYCLE")
            packet = self.team.packet(cycle["core"]["packet_id"])
            if actor["id"] in self.team._contributors(packet):raise HTTPException(403,"An independent reviewer must measure the review")
            timers = [r for r in self.records(parent=cycle["id"]) if r["core"]["kind"].startswith("TIMER_")]
            active = next((r for r in reversed(timers) if r["core"]["kind"] == "TIMER_START" and not any(t["core"].get("start_id") == r["id"] for t in timers)),None)
            if body["action"] == "START":
                if active:raise ValueError("This cycle already has an active review timer")
                return self._add(actor,"TIMER_START",cycle["id"],body,{})
            if not active or active["core"]["authenticated_context"]["actor_id"] != actor["id"]:
                raise ValueError("Finish or abandon your own active review timer")
            extra = dict(start_id=active["id"])
            if body["action"] == "FINISH":
                review = self.team._record(body["review_id"] or "")
                if review["core"]["kind"] != "SNAPSHOT_REVIEW" or review["core"]["parent_id"] != packet["id"] or review["core"]["authenticated_context"]["actor_id"] != actor["id"]:
                    raise ValueError("Select your review of this exact packet")
                if aware(review["core"]["recorded_at"]) < aware(active["core"]["recorded_at"]):raise ValueError("The review must be recorded after starting its timer")
                if any(t["core"].get("review_id") == review["id"] for t in timers):raise ValueError("This review round was already measured")
                seconds = (self.now()-aware(active["core"]["recorded_at"])).total_seconds()
                if not 0 < seconds <= 7*86400:raise ValueError("Timer must have positive elapsed time within seven days")
                extra.update(review_id=review["id"],elapsed_seconds=round(seconds,3),measurement="SERVER_ELAPSED_WALL_TIME_INCLUDES_IDLE")
            return self._add(actor,"TIMER_"+body["action"],cycle["id"],body,extra)

    def _accepted(self, cycle, review_id):
        review = self.team._record(review_id)
        latest = self.team.records("SNAPSHOT_REVIEW",cycle["core"]["packet_id"])
        if not latest or latest[0]["id"] != review_id or review["core"]["decision"] != "ACCEPT":
            raise ValueError("The latest independent review must accept this exact packet")
        reviewer = self.team.identity.user(review["core"]["authenticated_context"]["actor_id"])
        if not reviewer["active"] or "REVIEWER" not in reviewer["roles"] or reviewer["version"] != review["core"]["authenticated_context"]["actor_version"]:
            raise ValueError("The accepting reviewer's authority has changed")
        return review

    def plan(self, actor, payload):
        body = Plan.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor,"RESEARCHER")
            prior = self._prior(actor,"PAPER_PLAN",body)
            if prior:return prior
            cycle = self._get(body["cycle_id"],"CYCLE")
            self._accepted(cycle,body["review_id"])
            measured = self.records("TIMER_FINISH",cycle["id"])
            if not measured or measured[-1]["core"]["review_id"] != body["review_id"]:
                raise ValueError("Measure the current accepting review before freezing observation")
            p = self.team.protocol(cycle["core"]["protocol_id"])["core"]
            if p["build"] != CURRENT_BUILD:raise ValueError("Create a current-build cycle before planning observation")
            if self.records("PAPER_PLAN",cycle["id"]):raise ValueError("This cycle already has a frozen paper observation plan")
            start = aware(p["definition"]["rules"]["entry_at"])
            end = aware(p["request"]["observation_cutoff"])
            if not self.now() < start < end:raise ValueError("The fixed entry has passed; create and review a future-dated protocol")
            rows = self.team.workspace.research.catalog.rows(p["dataset_snapshot"]["id"])
            if any(aware(r.get("available_at") or r["timestamp"]) > self.now() for r in rows):
                raise ValueError("Future observations cannot be exposed before a prospective plan")
            return self._add(actor,"PAPER_PLAN",cycle["id"],body,dict(review_id=body["review_id"],
                paper_book_reference=body["paper_book_reference"],start_at=start.isoformat(),end_at=end.isoformat(),
                generation=self.team.identity.organization()["generation"],build=CURRENT_BUILD,
                authority="OBSERVATION_JOURNAL_ONLY",engine_execution_enabled=False))

    def observe(self, actor, payload):
        body = Observation.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor,"RESEARCHER")
            prior = self._prior(actor,"OBSERVATION",body)
            if prior:return prior
            plan = self._get(body["plan_id"],"PAPER_PLAN")
            p = plan["core"]
            cycle = self._get(p["parent_id"],"CYCLE")
            if p["generation"] != self.team.identity.organization()["generation"]:raise ValueError("Recovered observation plans require a newly reviewed cycle")
            if p["build"] != CURRENT_BUILD:raise ValueError("This plan belongs to an earlier build; retain it as historical evidence")
            self._accepted(cycle,p["review_id"])
            records = self.records("OBSERVATION",plan["id"])
            if any(r["core"]["observation_kind"] in {"ENDED","MATERIAL_CHANGE"} for r in records):raise ValueError("This observation plan has ended; retain the frozen history")
            at = aware(body["observed_at"])
            if not aware(p["start_at"]) <= at <= self.now():raise ValueError("Observation time must be after the planned start and no later than server receipt")
            if records and at < aware(records[-1]["core"]["observed_at"]):raise ValueError("Append observations in chronological order")
            end = aware(p["end_at"])
            if body["kind"] == "ENDED" and at < end:raise ValueError("The planned prospective interval has not actually elapsed")
            if body["kind"] != "ENDED" and at > end:raise ValueError("Observation is outside the frozen interval; end this plan or create a new cycle")
            return self._add(actor,"OBSERVATION",plan["id"],body,dict(observation_kind=body["kind"],observed_at=body["observed_at"],
                note=body["note"],evidence_reference=body["evidence_reference"],reported_pnl_paise=body["reported_pnl_paise"],
                timeliness="ON_TIME" if (self.now()-at).total_seconds() <= 86400 else "LATE_RECORDED",
                evidence_assurance="OPERATOR_RECORDED_NOT_ENGINE_VERIFIED",source_authenticity_verified=False))

    def correction(self, actor, payload):
        body = CorrectionReview.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor,"REVIEWER")
            prior = self._prior(actor,"CORRECTION_REVIEW",body)
            if prior:return prior
            item = self._get(body["observation_id"],"OBSERVATION")
            if item["core"]["observation_kind"] not in {"DATA_GAP","EXECUTION_DIFFERENCE","MATERIAL_CHANGE"}:raise ValueError("Select a documented discrepancy")
            if item["core"]["authenticated_context"]["actor_id"] == actor["id"]:raise HTTPException(403,"A discrepancy requires another reviewer's assessment")
            return self._add(actor,"CORRECTION_REVIEW",item["id"],body,{k:body[k] for k in ("decision","note","evidence_reference")})

    def effort(self, actor, payload):
        body = Effort.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor)
            prior = self._prior(actor,"EFFORT",body)
            if prior:return prior
            engagement = self._get(body["engagement_id"],"ENGAGEMENT")
            return self._add(actor,"EFFORT",engagement["id"],body,dict(effort_kind=body["kind"],minutes=body["minutes"],evidence_reference=body["evidence_reference"]))

    def _invoice_balance(self, identifier):
        self._get(identifier,"INVOICE")
        reversed_ids = {r["core"]["target_record_id"] for r in self.records("REVERSE_ENTRY")}
        records = [r for r in self.records(parent=identifier) if r["id"] not in reversed_ids]
        paid = sum(r["core"]["amount_paise"] for r in records if r["core"]["kind"] == "PAYMENT")
        refunded = sum(r["core"]["amount_paise"] for r in records if r["core"]["kind"] == "REFUND")
        return paid-refunded, any(r["core"]["kind"] == "VOID_INVOICE" for r in records)

    def finance(self, actor, payload):
        body = Finance.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor,"ADMIN")
            prior = self._prior(actor,body["kind"],body)
            if prior:return prior
            engagement = self._get(body["engagement_id"],"ENGAGEMENT")
            if aware(body["occurred_at"]) > self.now():raise ValueError("Record actual commercial events, not future payments or costs")
            if any(r["core"].get("reference") == body["reference"] for r in self.records() if r["core"]["kind"] in {"INVOICE","PAYMENT","REFUND","COST","VOID_INVOICE","REVERSE_ENTRY"}):
                raise ValueError("This commercial reference was already recorded")
            parent = engagement["id"]
            if body["kind"] == "REVERSE_ENTRY":
                target = self._get(body["target_record_id"])
                if target["core"]["kind"] not in {"PAYMENT","REFUND","COST"} or target["core"]["engagement_id"] != engagement["id"]:
                    raise ValueError("Correct a payment, refund or cost from this engagement")
                if self.records("REVERSE_ENTRY",target["id"]):raise ValueError("This entry was already corrected")
                if target["core"]["kind"] != "COST":
                    invoice = self._get(target["core"]["invoice_id"],"INVOICE")
                    paid,void = self._invoice_balance(invoice["id"])
                    adjusted = paid+target["core"]["amount_paise"]*(1 if target["core"]["kind"] == "REFUND" else -1)
                    if void or not 0 <= adjusted <= invoice["core"]["amount_paise"]:
                        raise ValueError("Correct related entries first so invoice receipts remain within their recorded bounds")
                parent = target["id"]
            if body["invoice_id"]:
                invoice = self._get(body["invoice_id"],"INVOICE")
                if invoice["core"]["engagement_id"] != engagement["id"]:raise ValueError("Invoice belongs to another engagement")
                paid, void = self._invoice_balance(invoice["id"])
                if void:raise ValueError("Invoice has been voided")
                if body["kind"] == "PAYMENT" and paid+body["amount_paise"] > invoice["core"]["amount_paise"]:raise ValueError("Payment exceeds the outstanding invoice balance")
                if body["kind"] == "REFUND" and body["amount_paise"] > paid:raise ValueError("Refund exceeds recorded net receipts")
                if body["kind"] == "VOID_INVOICE" and paid:raise ValueError("Refund receipts before voiding an invoice")
                parent = invoice["id"]
            if body["renews_invoice_id"]:
                old = self._get(body["renews_invoice_id"],"INVOICE")
                if old["core"]["engagement_id"] != engagement["id"] or aware(body["period_start"]) < aware(old["core"]["period_end"]):
                    raise ValueError("A renewal must follow the same engagement's preceding invoice period")
            return self._add(actor,body["kind"],parent,body,{k:v for k,v in body.items() if k not in {"request_id","kind"}})

    def demand(self, actor, payload):
        body = Demand.model_validate(payload).model_dump(mode="json")
        with self.team.lock:
            self._enter(actor)
            prior = self._prior(actor,"INTEGRATION_DEMAND",body)
            if prior:return prior
            engagement = self._get(body["engagement_id"],"ENGAGEMENT")
            return self._add(actor,"INTEGRATION_DEMAND",engagement["id"],body,{k:body[k] for k in ("integration","problem","evidence_reference")})

    def dashboard(self, actor):
        with self.team.lock:
            self._enter(actor)
            commercial = {"INVOICE","PAYMENT","REFUND","COST","VOID_INVOICE","REVERSE_ENTRY"}
            records = self.records()
            visible = records if "ADMIN" in actor["roles"] else [r for r in records if r["core"]["kind"] not in commercial]
            protocols = self.team.experiments.protocols()
            packets = []
            for protocol in protocols:
                for packet in self.team.experiments.packets(protocol["id"]):
                    packets.append(dict(id=packet["id"],protocol_id=protocol["id"],name=protocol["core"]["definition"]["name"],historical=packet["historical"]))
            return dict(schema_version=VERSION,records=visible,packet_options=packets,
                review_options=[r for r in self.team.records("SNAPSHOT_REVIEW")],
                report=self._report(actor),journal_only=True,live_orders_enabled=False)

    def _report(self, actor):
        records = self.records()
        real = {r["id"] for r in records if r["core"]["kind"] == "ENGAGEMENT" and r["core"]["evidence_kind"] == "CUSTOMER_REPORTED"}
        cycles = [r for r in records if r["core"]["kind"] == "CYCLE" and r["core"]["parent_id"] in real]
        cycle_ids = {r["id"] for r in cycles}
        measured = [r for r in records if r["core"]["kind"] == "TIMER_FINISH" and r["core"]["parent_id"] in cycle_ids]
        baselines = {r["id"]:r["core"]["baseline_review_seconds"] for r in cycles}
        durations = {}
        for record in measured:
            identifier = record["core"]["parent_id"]
            durations[identifier] = durations.get(identifier,0)+record["core"]["elapsed_seconds"]
        actual = list(durations.values())
        baseline = [baselines[identifier] for identifier in durations]
        plans = [r for r in records if r["core"]["kind"] == "PAPER_PLAN" and r["core"]["parent_id"] in cycle_ids]
        completed = []
        dates = []
        for plan in plans:
            events = self.records("OBSERVATION",plan["id"])
            dates.extend(min(aware(e["core"]["observed_at"]),aware(plan["core"]["end_at"])) for e in events
                if e["core"].get("timeliness") == "ON_TIME")
            if any(e["core"]["observation_kind"] == "ENDED" for e in events) and any(r["core"]["parent_id"] == plan["core"]["parent_id"] for r in measured):
                completed.append(plan["core"]["parent_id"])
        reversed_ids = {r["core"]["target_record_id"] for r in records if r["core"]["kind"] == "REVERSE_ENTRY"}
        commercial = [r for r in records if r["core"].get("engagement_id") in real and r["id"] not in reversed_ids]
        invoices = [r for r in commercial if r["core"]["kind"] == "INVOICE"]
        paying = []
        renewals = []
        for invoice in invoices:
            paid, void = self._invoice_balance(invoice["id"])
            if not void and paid >= invoice["core"]["amount_paise"]:
                paying.append(invoice["id"])
                if invoice["core"].get("renews_invoice_id"):
                    old = self._get(invoice["core"]["renews_invoice_id"],"INVOICE")
                    old_paid, old_void = self._invoice_balance(old["id"])
                    if not old_void and old_paid >= old["core"]["amount_paise"]:renewals.append(invoice["id"])
        payment = sum(r["core"]["amount_paise"] for r in commercial if r["core"]["kind"] == "PAYMENT")
        refunds = sum(r["core"]["amount_paise"] for r in commercial if r["core"]["kind"] == "REFUND")
        costs = sum(r["core"]["amount_paise"] for r in commercial if r["core"]["kind"] == "COST")
        span = (max(dates)-min(dates)).total_seconds()/86400 if dates else 0
        weeks = len({(d.isocalendar().year,d.isocalendar().week) for d in dates})
        seconds_baseline = median(baseline) if baseline else None
        seconds_actual = median(actual) if actual else None
        demands = sorted({r["core"]["integration"] for r in records if r["core"]["kind"] == "INTEGRATION_DEMAND" and r["core"]["parent_id"] in real})
        plan_ids = {p["id"] for p in plans}
        discrepancies = {r["id"] for r in records if r["core"]["kind"] == "OBSERVATION" and r["core"]["parent_id"] in plan_ids
            and r["core"]["observation_kind"] in {"DATA_GAP","EXECUTION_DIFFERENCE","MATERIAL_CHANGE"}}
        assessments = {r["core"]["parent_id"]:r["core"]["decision"] for r in records if r["core"]["kind"] == "CORRECTION_REVIEW" and r["core"]["parent_id"] in discrepancies}
        core = dict(schema_version=VERSION,organization_id=self.team.identity.organization()["id"],exported_at=self.now().isoformat(),build=CURRENT_BUILD,
            evidence_assurance="CUSTOMER_REPORTED_AND_SERVER_RECORDED_NOT_INDEPENDENTLY_VERIFIED",mvp5_accepted=False,
            controlled_exercises_excluded=True,customer_engagements=len(real),measured_reviews=len(durations),measured_review_rounds=len(measured),
            baseline_median_seconds=seconds_baseline,artha_median_elapsed_seconds=seconds_actual,
            review_time_reduction_pct=(seconds_baseline-seconds_actual)/seconds_baseline*100 if baseline else None,
            completed_journeys=len(set(completed)),observation_span_days=span,active_iso_weeks=weeks,
            documented_discrepancies=len(discrepancies),confirmed_corrections=sum(v == "CONFIRMED" for v in assessments.values()),
            repeated_four_week_usage=len(set(completed)) >= 2 and span >= 28 and weeks >= 4,
            paying_organization=bool(paying),renewed_organization=bool(renewals),integration_demand=demands,
            external_acceptance_pending=True,source_authenticity_verified=False,
            commercial=dict(invoice_count=len(invoices),fully_paid_invoices=len(paying),paid_renewals=len(renewals),
                payments_paise=payment,refunds_paise=refunds,delivery_costs_paise=costs,net_receipts_paise=payment-refunds,
                cost_completeness="UNVERIFIED",delivery_cost_records=sum(r["core"]["kind"] == "COST" for r in commercial),
                net_cash_after_recorded_costs_paise=payment-refunds-costs) if "ADMIN" in actor["roles"] else None,
            effort_minutes={kind:sum(r["core"]["minutes"] for r in records if r["core"]["kind"] == "EFFORT" and r["core"]["parent_id"] in real and r["core"]["effort_kind"] == kind) for kind in ("ONBOARDING","SUPPORT","REVIEW")},
            audit=check_customer_database(self.db))
        if "ADMIN" not in actor["roles"]:
            for key in ("paying_organization","renewed_organization"):core.pop(key)
        return dict(core=core,report_hash=digest(core))

    def report(self, actor):
        with self.team.lock:
            self._enter(actor)
            return self._report(actor)
