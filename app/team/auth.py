"""One organization per store; password hashes, revocable sessions and audited membership."""
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl,hashlib,hmac,json,os,secrets,sqlite3,threading,time,uuid
from pathlib import Path
from fastapi import HTTPException
from ..research.catalog import canonical,digest

ROLES={"ADMIN","RESEARCHER","REVIEWER"}
SCRYPT_N=2**17
HASH_SLOTS=threading.BoundedSemaphore(2)
SESSION_SECONDS=8*3600
IDLE_SECONDS=1800

def secret_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password,salt=None):
    if not isinstance(password,str) or not 15<=len(password)<=256 or len(password.encode())>1024:
        raise ValueError("Use a password between 15 and 256 characters")
    salt=salt or secrets.token_hex(16)
    with HASH_SLOTS:result=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=SCRYPT_N,r=8,p=1,maxmem=256*1024*1024,dklen=32)
    return dict(salt=salt,hash=result.hex())

def public_user(user):
    return {key:user[key] for key in ("id","email","name","roles","active","version")}

def check_auth_database(db):
    previous="0"*64;latest={};count=0;organization_hash=None
    for sequence,raw in db.execute("SELECT sequence,document FROM team_identity_events ORDER BY sequence"):
        item=json.loads(raw);expected=item.pop("hash");count+=1
        if sequence!=count or item["sequence"]!=count or item["previous_hash"]!=previous or digest(item)!=expected:
            raise ValueError("Team identity event integrity failed")
        previous=expected
        if "user_hash" in item["details"]:latest[item["subject_id"]]=item["details"]["user_hash"]
        if "organization_hash" in item["details"]:organization_hash=item["details"]["organization_hash"]
    for identifier,raw in db.execute("SELECT id,document FROM team_identity_users"):
        item=json.loads(raw)
        if item["id"]!=identifier or digest(item)!=latest.get(identifier):
            raise ValueError("Team membership integrity failed")
    row=db.execute("SELECT document FROM team_identity_settings WHERE name='organization'").fetchone()
    if row and digest(json.loads(row[0]))!=organization_hash:raise ValueError("Team organization integrity failed")
    return dict(users_verified=len(latest),events_verified=count)

class StoreLock:
    """Reentrant within a thread, exclusive across server and operator processes."""
    def __init__(self,path):
        self.thread=threading.RLock();self.local=threading.local();self.file=open(path,"a")
        Path(path).chmod(0o600)
    def __enter__(self):
        self.thread.acquire()
        try:
            if not getattr(self.local,"depth",0):fcntl.flock(self.file,fcntl.LOCK_EX)
            self.local.depth=getattr(self.local,"depth",0)+1
        except BaseException:
            self.thread.release();raise
        return self
    def __exit__(self,*args):
        self.local.depth-=1
        if not self.local.depth:fcntl.flock(self.file,fcntl.LOCK_UN)
        self.thread.release()
    def close(self):self.file.close()

class Identity:
    def __init__(self,root,clock=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.clock=clock or time.time;self.lock=StoreLock(self.root/"organization.lock")
        self.db=sqlite3.connect(self.root/"identity.sqlite3",check_same_thread=False,timeout=20)
        self.db.execute("PRAGMA journal_mode=WAL");self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS team_identity_settings(name TEXT PRIMARY KEY,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS team_identity_users(id TEXT PRIMARY KEY,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS team_identity_invites(token_hash TEXT PRIMARY KEY,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS team_identity_sessions(token_hash TEXT PRIMARY KEY,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS team_identity_limits(key TEXT PRIMARY KEY,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS team_identity_events(sequence INTEGER PRIMARY KEY,document TEXT NOT NULL);
        """)
        (self.root/"identity.sqlite3").chmod(0o600)
        with self.lock:check_auth_database(self.db)
        self.cookie="artha_team_"+hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:12]
        if not self.organization() and not (self.root/"bootstrap.key").exists():
            try:
                with open(self.root/"bootstrap.key","x",opener=lambda path,flags:os.open(path,flags,0o600)) as stream:stream.write(secrets.token_urlsafe(40))
            except FileExistsError:pass

    def organization(self):
        with self.lock:row=self.db.execute("SELECT document FROM team_identity_settings WHERE name='organization'").fetchone()
        return json.loads(row[0]) if row else None

    def _event(self,kind,actor,subject,details):
        row=self.db.execute("SELECT document FROM team_identity_events ORDER BY sequence DESC LIMIT 1").fetchone()
        prior=json.loads(row[0]) if row else None
        item=dict(sequence=prior["sequence"]+1 if prior else 1,previous_hash=prior["hash"] if prior else "0"*64,
            time=self.clock(),kind=kind,actor_id=actor,subject_id=subject,details=details)
        item["hash"]=digest(item)
        self.db.execute("INSERT INTO team_identity_events VALUES(?,?)",(item["sequence"],canonical(item)))

    def _save_user(self,user,kind,actor):
        self.db.execute("INSERT OR REPLACE INTO team_identity_users VALUES(?,?)",(user["id"],canonical(user)))
        self._event(kind,actor,user["id"],dict(user_hash=digest(user),user=public_user(user)))

    def user(self,identifier):
        row=self.db.execute("SELECT document FROM team_identity_users WHERE id=?",(identifier,)).fetchone()
        if not row:raise HTTPException(401,"Sign in required")
        user=json.loads(row[0])
        latest=self.db.execute("SELECT document FROM team_identity_events WHERE json_extract(document,'$.subject_id')=? AND json_extract(document,'$.details.user_hash') IS NOT NULL ORDER BY sequence DESC LIMIT 1",(identifier,)).fetchone()
        if not latest or json.loads(latest[0])["details"]["user_hash"]!=digest(user):
            raise HTTPException(503,"Team identity integrity check failed")
        return user

    def require(self,actor,*roles):
        user=self.user(actor["id"])
        if not user["active"] or user["version"]!=actor["version"]:raise HTTPException(401,"Identity changed; sign in again")
        if roles and not set(roles)&set(user["roles"]):raise HTTPException(403,"This action requires the assigned team role")
        return public_user(user)

    def bootstrap(self,key,name,email,password):
        hashed=password_hash(password)
        email=email.strip().lower()
        with self.lock,self.db:
            if self.organization():raise HTTPException(409,"Organization already initialized")
            path=self.root/"bootstrap.key"
            if not path.exists() or not hmac.compare_digest(secret_hash(key),secret_hash(path.read_text().strip())):
                raise HTTPException(403,"Invalid setup credential")
            org=dict(id=uuid.uuid4().hex,name=name,generation=1,created_at=self.clock())
            user=dict(id=uuid.uuid4().hex,email=email,name=email,roles=["ADMIN","RESEARCHER","REVIEWER"],active=True,version=1,password=hashed)
            self.db.execute("INSERT INTO team_identity_settings VALUES('organization',?)",(canonical(org),))
            self._event("ORGANIZATION_CREATED",user["id"],org["id"],dict(organization_hash=digest(org)))
            self._save_user(user,"ADMIN_BOOTSTRAPPED",user["id"])
        path.unlink(missing_ok=True)
        return public_user(user)

    def _throttle(self,key,limit=5):
        row=self.db.execute("SELECT document FROM team_identity_limits WHERE key=?",(key,)).fetchone()
        item=json.loads(row[0]) if row else dict(start=self.clock(),count=0)
        if self.clock()-item["start"]>=900:item=dict(start=self.clock(),count=0)
        if item["count"]>=limit:raise HTTPException(429,"Too many attempts; retry after the cooldown")
        item["count"]+=1
        self.db.execute("INSERT OR REPLACE INTO team_identity_limits VALUES(?,?)",(key,canonical(item)))

    def login(self,email,password,peer):
        email=email.strip().lower();key=secret_hash("login:"+email)
        with self.lock,self.db:
            self._throttle(key);self._throttle(secret_hash("peer:"+peer),100)
        with self.lock:
            row=self.db.execute("SELECT document FROM team_identity_users WHERE json_extract(document,'$.email')=?",(email,)).fetchone()
            user=json.loads(row[0]) if row else None
        stored=user["password"] if user and user.get("password") else dict(salt="00"*16,hash="00"*32)
        try:actual=password_hash(password,stored["salt"])["hash"]
        except ValueError:actual="invalid"
        with self.lock,self.db:
            if not user or not user["active"] or not hmac.compare_digest(actual,stored["hash"]):
                raise HTTPException(401,"Email or password was not accepted")
            self.require(public_user(user))
            token=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32)
            item=dict(user_id=user["id"],version=user["version"],csrf=csrf,issued=self.clock(),last_seen=self.clock(),expires=self.clock()+SESSION_SECONDS)
            self.db.execute("INSERT INTO team_identity_sessions VALUES(?,?)",(secret_hash(token),canonical(item)))
            self.db.execute("DELETE FROM team_identity_sessions WHERE json_extract(document,'$.expires')<=?",(self.clock(),))
            stale=self.db.execute("SELECT token_hash FROM team_identity_sessions WHERE json_extract(document,'$.user_id')=? ORDER BY json_extract(document,'$.issued') DESC LIMIT -1 OFFSET 8",(user["id"],)).fetchall()
            self.db.executemany("DELETE FROM team_identity_sessions WHERE token_hash=?",stale)
            self.db.execute("DELETE FROM team_identity_limits WHERE key=?",(key,))
            self._event("SIGNED_IN",user["id"],user["id"],{})
        return token,csrf,public_user(user)

    def authenticate(self,token,csrf=None,mutation=False):
        if not token:raise HTTPException(401,"Sign in required")
        with self.lock,self.db:
            row=self.db.execute("SELECT document FROM team_identity_sessions WHERE token_hash=?",(secret_hash(token),)).fetchone()
            if not row:raise HTTPException(401,"Session expired; sign in again")
            item=json.loads(row[0]);now=self.clock()
            if now>=item["expires"] or now-item["last_seen"]>=IDLE_SECONDS:
                self.db.execute("DELETE FROM team_identity_sessions WHERE token_hash=?",(secret_hash(token),))
                raise HTTPException(401,"Session expired; sign in again")
            user=self.user(item["user_id"])
            if not user["active"] or user["version"]!=item["version"]:raise HTTPException(401,"Identity changed; sign in again")
            if mutation and (not csrf or not hmac.compare_digest(csrf,item["csrf"])):raise HTTPException(403,"Refresh the authenticated session before retrying")
            item["last_seen"]=now
            self.db.execute("UPDATE team_identity_sessions SET document=? WHERE token_hash=?",(canonical(item),secret_hash(token)))
            return dict(user=public_user(user),csrf=item["csrf"],organization=self.organization())

    def logout(self,token,actor):
        with self.lock,self.db:
            self.db.execute("DELETE FROM team_identity_sessions WHERE token_hash=?",(secret_hash(token),))
            self._event("SIGNED_OUT",actor["id"],actor["id"],{})

    def members(self,actor):
        with self.lock:
            self.require(actor,"ADMIN")
            return [public_user(self.user(r[0])) for r in self.db.execute("SELECT id FROM team_identity_users ORDER BY rowid")]

    def invite(self,actor,email,name,roles):
        with self.lock,self.db:
            self.require(actor,"ADMIN")
            if self.db.execute("SELECT count(*) FROM team_identity_users").fetchone()[0]>=100:raise ValueError("Organization member limit reached")
            email=email.lower().strip()
            if self.db.execute("SELECT 1 FROM team_identity_users WHERE json_extract(document,'$.email')=?",(email,)).fetchone():raise ValueError("This email already has a membership")
            user=dict(id=uuid.uuid4().hex,email=email,name=name,roles=sorted(set(roles)),active=False,version=1,password=None)
            self._save_user(user,"MEMBER_INVITED",actor["id"])
            token=secrets.token_urlsafe(40);item=dict(user_id=user["id"],version=1,expires=self.clock()+86400,used=False)
            self.db.execute("INSERT INTO team_identity_invites VALUES(?,?)",(secret_hash(token),canonical(item)))
            return dict(user=public_user(user),invitation_token=token,expires_at=item["expires"])

    def renew_invite(self,actor,identifier):
        with self.lock,self.db:
            self.require(actor,"ADMIN");user=self.user(identifier)
            if user["active"] and "ADMIN" in user["roles"]:
                admins=[json.loads(row[0]) for row in self.db.execute("SELECT document FROM team_identity_users")]
                if sum(item["active"] and "ADMIN" in item["roles"] for item in admins)<=1:raise ValueError("Use local operator recovery for the last active administrator")
            user.update(active=False,version=user["version"]+1)
            self._save_user(user,"INVITATION_REISSUED",actor["id"])
            self.db.execute("DELETE FROM team_identity_sessions WHERE json_extract(document,'$.user_id')=?",(identifier,))
            token=secrets.token_urlsafe(40);item=dict(user_id=identifier,version=user["version"],expires=self.clock()+86400,used=False)
            self.db.execute("INSERT INTO team_identity_invites VALUES(?,?)",(secret_hash(token),canonical(item)))
            return dict(user=public_user(user),invitation_token=token,expires_at=item["expires"])

    def accept(self,token,password):
        hashed=password_hash(password)
        with self.lock,self.db:
            row=self.db.execute("SELECT document FROM team_identity_invites WHERE token_hash=?",(secret_hash(token),)).fetchone()
            item=json.loads(row[0]) if row else None
            if not item or item["used"] or item["expires"]<=self.clock():raise HTTPException(400,"Invitation is invalid or expired")
            user=self.user(item["user_id"])
            if user["version"]!=item["version"]:raise HTTPException(400,"Invitation is invalid or expired")
            user.update(password=hashed,active=True,version=user["version"]+1)
            item["used"]=True
            self.db.execute("UPDATE team_identity_invites SET document=? WHERE token_hash=?",(canonical(item),secret_hash(token)))
            self._save_user(user,"INVITATION_ACCEPTED",user["id"])
        return public_user(user)

    def change_member(self,actor,identifier,roles,active):
        with self.lock,self.db:
            self.require(actor,"ADMIN");user=self.user(identifier)
            if user["active"] and "ADMIN" in user["roles"] and (not active or "ADMIN" not in roles):
                admins=[json.loads(r[0]) for r in self.db.execute("SELECT document FROM team_identity_users")]
                if sum(x["active"] and "ADMIN" in x["roles"] for x in admins)<=1:raise ValueError("Keep at least one active administrator")
            if active and not user["password"]:raise ValueError("The invitee must set their own password before activation")
            user.update(roles=sorted(set(roles)),active=active,version=user["version"]+1)
            self._save_user(user,"MEMBERSHIP_CHANGED",actor["id"])
            self.db.execute("DELETE FROM team_identity_sessions WHERE json_extract(document,'$.user_id')=?",(identifier,))
        return public_user(user)

    def change_password(self,actor,current,new):
        with self.lock:
            user=self.user(actor["id"])
            if not hmac.compare_digest(password_hash(current,user["password"]["salt"])["hash"],user["password"]["hash"]):
                raise HTTPException(401,"Current password was not accepted")
        hashed=password_hash(new)
        with self.lock,self.db:
            self.require(actor);user=self.user(actor["id"]);user.update(password=hashed,version=user["version"]+1)
            self._save_user(user,"PASSWORD_CHANGED",actor["id"])
            self.db.execute("DELETE FROM team_identity_sessions WHERE json_extract(document,'$.user_id')=?",(actor["id"],))

    def recover_admin(self,email,password):
        hashed=password_hash(password)
        with self.lock,self.db:
            row=self.db.execute("SELECT id FROM team_identity_users WHERE json_extract(document,'$.email')=?",(email.lower().strip(),)).fetchone()
            if not row:raise ValueError("Administrator recovery requires an existing member")
            user=self.user(row[0]);user.update(password=hashed,roles=sorted(set(user["roles"])|{"ADMIN"}),active=True,version=user["version"]+1)
            self._save_user(user,"LOCAL_OPERATOR_RECOVERY","LOCAL_FILESYSTEM_OPERATOR")
            self.db.execute("DELETE FROM team_identity_sessions")
        return public_user(user)

    def close(self):
        self.db.close();self.lock.close()
