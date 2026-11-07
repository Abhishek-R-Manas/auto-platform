"""Organization-owned research and independent evidence/scope reviews."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
import json,sqlite3,threading,uuid
from pathlib import Path
from types import SimpleNamespace
from fastapi import HTTPException
from ..contracts import ContractBook
from ..contract_research import ContractResearch,hashed_record,verify_record
from ..research.catalog import canonical,digest
from ..research.jobs import Research,CURRENT_BUILD
from ..research.schemas import Risk
from .auth import Identity

def check_review_database(db):
    count=0;previous="0"*64;indexed={}
    for sequence,raw in db.execute("SELECT sequence,document FROM team_review_events ORDER BY sequence"):
        event=json.loads(raw);expected=event.pop("hash");count+=1
        if sequence!=count or event["sequence"]!=count or event["previous_hash"]!=previous or digest(event)!=expected:
            raise ValueError("Team review event integrity failed")
        previous=expected;indexed[event["record_id"]]=event["record_id"]
    records={}
    for identifier,kind,parent,raw in db.execute("SELECT id,kind,parent_id,document FROM team_review_records"):
        item=verify_record(json.loads(raw))
        if item["id"]!=identifier or item["core"]["kind"]!=kind or item["core"]["parent_id"]!=parent or identifier not in indexed:
            raise ValueError("Team review record integrity failed")
        records[identifier]=item
    for expected,identifier in db.execute("SELECT input_hash,record_id FROM team_review_requests"):
        if identifier not in records or digest(records[identifier]["core"]["inputs"])!=expected:
            raise ValueError("Team review request integrity failed")
    return dict(records_verified=len(records),events_verified=count)

class Team:
    def __init__(self,root,clock=None):
        self.root=Path(root)
        if any((self.root/name).exists() for name in ("paper.sqlite3","pilot","market-data")):
            raise ValueError("Use a dedicated organization directory, separate from the local owner's store")
        self.identity=Identity(root,clock);self.lock=self.identity.lock;self._workspace=None

    @property
    def workspace(self):
        with self.lock:
            if self._workspace is None:
                db=sqlite3.connect(self.root/"workspace.sqlite3",check_same_thread=False,timeout=20)
                db.execute("PRAGMA journal_mode=WAL");db.execute("PRAGMA synchronous=FULL")
                research=Research(self.root/"research")
                self._workspace=SimpleNamespace(db=db,lock=threading.RLock(),research=research)
                self.book=ContractBook(self._workspace);self.experiments=ContractResearch(self._workspace)
                db.executescript("""
                  CREATE TABLE IF NOT EXISTS team_review_records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,parent_id TEXT NOT NULL,document TEXT NOT NULL);
                  CREATE INDEX IF NOT EXISTS team_review_parent ON team_review_records(kind,parent_id);
                  CREATE TABLE IF NOT EXISTS team_review_requests(request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,record_id TEXT NOT NULL);
                  CREATE TABLE IF NOT EXISTS team_review_events(sequence INTEGER PRIMARY KEY,document TEXT NOT NULL);
                """)
                (self.root/"workspace.sqlite3").chmod(0o600)
                from .customer import CustomerOperations
                self.customers=CustomerOperations(self)
            return self._workspace

    def context(self,actor):
        return dict(organization_id=self.identity.organization()["id"],actor_id=actor["id"],actor_version=actor["version"],roles=actor["roles"])

    def request_key(self,actor,request_id):
        return digest(dict(actor_id=actor["id"],request_id=request_id))

    def _owned(self,item):
        ctx=item["core"].get("authenticated_context")
        if not ctx or ctx["organization_id"]!=self.identity.organization()["id"]:
            raise HTTPException(404,"Organization record not found")
        return item

    def protocol(self,identifier):
        self.workspace
        try:return self._owned(self.experiments.get_protocol(identifier))
        except ValueError:raise HTTPException(404,"Organization protocol not found") from None

    def packet(self,identifier):
        self.workspace
        try:
            item=self.experiments._get("mvp_packets",identifier)
            self._owned(item["core"]["packet"])
            self.protocol(item["core"]["protocol_id"])
            return item
        except ValueError:raise HTTPException(404,"Organization packet not found") from None

    def _record(self,identifier):
        row=self.workspace.db.execute("SELECT document FROM team_review_records WHERE id=?",(identifier,)).fetchone()
        if not row:raise HTTPException(404,"Organization review not found")
        item=verify_record(json.loads(row[0]))
        if item["id"]!=identifier or item["core"]["organization_id"]!=self.identity.organization()["id"]:
            raise HTTPException(404,"Organization review not found")
        return item

    def records(self,kind=None,parent=None):
        query="SELECT id FROM team_review_records WHERE 1=1";args=[]
        if kind:query+=" AND kind=?";args.append(kind)
        if parent:query+=" AND parent_id=?";args.append(parent)
        query+=" ORDER BY rowid DESC LIMIT 1000"
        return [self._record(row[0]) for row in self.workspace.db.execute(query,args)]

    def _add(self,kind,parent,actor,body,extra=None):
        request_id=body["request_id"]
        inputs=dict(actor_id=actor["id"],kind=kind,body={k:v for k,v in body.items() if k!="request_id"})
        key=self.request_key(actor,request_id)
        with self.workspace.lock,self.workspace.db:
            prior=self.workspace.db.execute("SELECT input_hash,record_id FROM team_review_requests WHERE request_id=?",(key,)).fetchone()
            if prior:
                if prior[0]!=digest(inputs):raise ValueError("Request ID already binds different review inputs")
                return self._record(prior[1])
            core=dict(kind=kind,parent_id=parent,inputs=inputs,organization_id=self.identity.organization()["id"],
                authenticated_context=self.context(actor),recorded_at=datetime.fromtimestamp(self.identity.clock(),timezone.utc).isoformat(),nonce=uuid.uuid4().hex,**(extra or {}))
            item=hashed_record(core)
            self.workspace.db.execute("INSERT INTO team_review_records VALUES(?,?,?,?)",(item["id"],kind,parent,canonical(item)))
            self.workspace.db.execute("INSERT INTO team_review_requests VALUES(?,?,?)",(key,digest(inputs),item["id"]))
            row=self.workspace.db.execute("SELECT document FROM team_review_events ORDER BY sequence DESC LIMIT 1").fetchone()
            old=json.loads(row[0]) if row else None
            event=dict(sequence=old["sequence"]+1 if old else 1,previous_hash=old["hash"] if old else "0"*64,
                record_id=item["id"],kind=kind,actor_id=actor["id"],organization_id=core["organization_id"])
            event["hash"]=digest(event)
            self.workspace.db.execute("INSERT INTO team_review_events VALUES(?,?)",(event["sequence"],canonical(event)))
        return item

    def dashboard(self,actor):
        with self.lock:
            self.identity.require(actor);self.workspace
            protocols=[p for p in self.experiments.protocols() if p["core"].get("authenticated_context",{}).get("organization_id")==self.identity.organization()["id"]]
            return dict(organization=self.identity.organization(),user=actor,contracts=[self.book.get(item["id"]) for item in self.book.list()],risk_defaults=Risk().model_dump(mode="json"),datasets=self.workspace.research.catalog.datasets(),
                protocols=protocols,reviews=self.records(),build=CURRENT_BUILD,live_orders_enabled=False,
                identity_assurance="AUTHENTICATED_ORGANIZATION",ai_required_for_research=False)

    def example(self,actor,request_id):
        from ..research.fixtures import contract_fixture
        with self.lock:
            self.identity.require(actor,"RESEARCHER");self.workspace
            definition,body=contract_fixture();dataset=self.workspace.research.catalog.import_data(body)
            saved=self.book.save(dict(request_id=self.request_key(actor,request_id),definition=definition,dataset_id=dataset["id"]),identity=self.context(actor))
            return dict(contract=saved,dataset_id=dataset["id"],controlled_example=True)

    def save(self,actor,body):
        with self.lock:
            self.identity.require(actor,"RESEARCHER");self.workspace
            body=dict(body);body["request_id"]=self.request_key(actor,body["request_id"])
            if body.get("parent_id"):self._owned(self.book.get(body["parent_id"]))
            return self.book.save(body,identity=self.context(actor))

    def import_data(self,actor,body):
        with self.lock:
            self.identity.require(actor,"RESEARCHER")
            return self.workspace.research.catalog.import_data(body)

    def freeze(self,actor,body):
        with self.lock:
            self.identity.require(actor,"RESEARCHER");self.workspace
            self._owned(self.book.get(body["contract_id"]))
            body=dict(body,request_id=self.request_key(actor,body["request_id"]),organization_ref=self.identity.organization()["id"])
            return self.experiments.freeze(body,identity=self.context(actor))

    def detail(self,actor,identifier):
        with self.lock:
            self.identity.require(actor);p=self.protocol(identifier)
            return dict(protocol=p,trials=self.experiments.trials(identifier),packets=self.experiments.packets(identifier),
                reviews=self.records(),current_build=p["core"]["build"]==CURRENT_BUILD)

    def _contributors(self,packet):
        core=packet["core"]["packet"]["core"];p=core["protocol"]
        people={core["authenticated_context"]["actor_id"],p["core"]["authenticated_context"]["actor_id"]}
        people.add(self.book.get(p["core"]["contract_id"])["core"]["authenticated_context"]["actor_id"])
        for trial in core["trials"]:
            people.add(trial["authenticated_context"]["actor_id"])
        return sorted(people)

    def review(self,actor,body):
        with self.lock:
            self.identity.require(actor,"REVIEWER");packet=self.packet(body["packet_id"])
            people=self._contributors(packet)
            if actor["id"] in people:raise HTTPException(403,"An independent reviewer must review work contributed by another member")
            return self._add("SNAPSHOT_REVIEW",body["packet_id"],actor,body,
                dict(protocol_id=packet["core"]["protocol_id"],packet_hash=packet["core"]["packet"]["packet_hash"],
                    contributors=people,decision=body["decision"],comment=body["comment"],authority="EVIDENCE_SNAPSHOT_ONLY"))

    def comment(self,actor,body):
        with self.lock:
            self.identity.require(actor);self.packet(body["packet_id"])
            return self._add("COMMENT",body["packet_id"],actor,body,dict(comment=body["comment"]))

    def grant(self,actor,body):
        with self.lock:
            self.identity.require(actor,"REVIEWER")
            packet=self.packet(body["packet_id"]);p=self.protocol(packet["core"]["protocol_id"])
            if self.experiments.packet_summary(packet)["historical"]:raise ValueError("Review a current packet before granting a new scope")
            if actor["id"] in self._contributors(packet):raise HTTPException(403,"A contributor cannot approve their own operating scope")
            review=self._record(body["review_id"])
            latest=self.records("SNAPSHOT_REVIEW",body["packet_id"])
            if not latest or latest[0]["id"]!=review["id"] or review["core"]["decision"]!="ACCEPT":
                raise ValueError("The current independent snapshot review must accept this exact packet")
            reviewer=self.identity.user(review["core"]["authenticated_context"]["actor_id"])
            if not reviewer["active"] or "REVIEWER" not in reviewer["roles"] or reviewer["version"]!=review["core"]["authenticated_context"]["actor_version"]:raise ValueError("The snapshot reviewer is no longer authorized")
            now=datetime.fromtimestamp(self.identity.clock(),timezone.utc)
            until=datetime.fromisoformat(body["expires_at"])
            if not now<until<=now+timedelta(days=7):raise ValueError("Scope expiry must be within the next seven days")
            remaining=p["core"]["max_trials"]-len(self.experiments.trials(p["id"]))
            if body["max_new_trials"]>remaining:raise ValueError("Scope exceeds the remaining frozen trial budget")
            if not body["acknowledge_unqualified_research"]:raise ValueError("Acknowledge the packet's unqualified research limits")
            bindings=dict(protocol_id=p["id"],contract_id=p["core"]["contract_id"],definition_hash=p["core"]["definition_hash"],
                dataset_id=p["core"]["dataset_snapshot"]["id"],dataset_hash=p["core"]["dataset_snapshot"]["parquet_hash"],
                build_hash=p["core"]["build"]["code_hash"],risk_policy_hash=p["core"]["risk_policy_hash"])
            return self._add("SCOPE_GRANT",p["id"],actor,body,
                dict(bindings=bindings,review_id=review["id"],packet_id=packet["id"],
                    generation=self.identity.organization()["generation"],expires_at=body["expires_at"],
                    max_new_trials=body["max_new_trials"],scenarios=body["scenarios"],authority="BOUNDED_PAPER_RESEARCH_ONLY"))

    def revoke(self,actor,body):
        with self.lock:
            self.identity.require(actor,"REVIEWER","ADMIN");grant=self._record(body["grant_id"])
            if grant["core"]["kind"]!="SCOPE_GRANT":raise ValueError("Select an operating scope")
            return self._add("SCOPE_REVOKED",grant["id"],actor,body,dict(comment=body["comment"]))

    def start(self,actor,body):
        from .gates import authority_reason
        with self.lock:
            self.identity.require(actor,"RESEARCHER");p=self.protocol(body["protocol_id"])
            grant_id=body.get("grant_id")
            existing=self.workspace.db.execute("SELECT record_id FROM team_review_requests WHERE request_id=?",(self.request_key(actor,body["request_id"]),)).fetchone()
            if existing:
                reservation=self._add("EXECUTION_RESERVED",grant_id or p["id"],actor,body)
                prior=self.workspace.db.execute("SELECT object_id FROM mvp_research_requests WHERE request_id=? AND kind='TRIAL'",(self.request_key(actor,body["request_id"]),)).fetchone()
                if prior:
                    attempt=self.experiments.trial(prior[0])
                    if attempt["run_id"]:return attempt
            preflight=dict(organization_id=self.identity.organization()["id"],actor_id=actor["id"],actor_version=actor["version"],
                generation=self.identity.organization()["generation"],operation_id=existing[0] if existing else None,
                grant_id=grant_id,protocol_id=p["id"],scenario=body["kind"])
            reason=authority_reason(self.root,preflight,now=self.identity.clock())
            if reason:raise HTTPException(403,reason)
            if not existing:
                reservation=self._add("EXECUTION_RESERVED",grant_id or p["id"],actor,body,
                    dict(protocol_id=p["id"],grant_id=grant_id,scenario=body["kind"],generation=self.identity.organization()["generation"]))
            auth=dict(organization_id=self.identity.organization()["id"],actor_id=actor["id"],actor_version=actor["version"],
                generation=self.identity.organization()["generation"],operation_id=reservation["id"],grant_id=grant_id,
                protocol_id=p["id"],scenario=body["kind"])
            reason=authority_reason(self.root,auth,now=self.identity.clock())
            if reason:raise HTTPException(403,reason)
            request={k:v for k,v in body.items() if k!="grant_id"};request["request_id"]=self.request_key(actor,body["request_id"])
            return self.experiments.start(request,identity=self.context(actor),authorization=auth)

    def control(self,actor,identifier,action):
        from .gates import authority_reason
        with self.lock:
            self.identity.require(actor,"RESEARCHER","ADMIN");self.workspace
            trial=self._owned(self.experiments.trial(identifier))
            if actor["id"]!=trial["core"]["authenticated_context"]["actor_id"] and "ADMIN" not in actor["roles"]:
                raise HTTPException(403,"Only the initiating researcher or administrator can control this attempt")
            if action=="RESUME":
                reason=authority_reason(self.root,trial["core"]["authorization"],now=self.identity.clock())
                if reason:raise HTTPException(403,reason)
            return self.experiments.control(identifier,action)

    def recover(self,actor,identifier):
        from .gates import authority_reason
        with self.lock:
            self.identity.require(actor,"RESEARCHER");self.workspace
            item=self._owned(self.experiments.trial(identifier))
            if actor["id"]!=item["core"]["authenticated_context"]["actor_id"]:raise HTTPException(403,"Recover your own reserved attempt")
            reason=authority_reason(self.root,item["core"]["authorization"],now=self.identity.clock())
            if reason:raise HTTPException(403,reason)
            return self.experiments.dispatch(identifier)

    def capture(self,actor,body):
        with self.lock:
            self.identity.require(actor,"RESEARCHER");self.protocol(body["protocol_id"])
            request=dict(body,request_id=self.request_key(actor,body["request_id"]))
            return self.experiments.packet_summary(self.experiments.capture(request,identity=self.context(actor)))

    def export(self,actor,identifier,trace=False,ack=False):
        with self.lock:
            self.identity.require(actor);self.packet(identifier)
            return self.experiments.export(identifier,trace,ack,allow_historical=True)

    def close(self):
        if self._workspace:
            self._workspace.research.close();self._workspace.db.close()
        self.identity.close()
