"""Private verified organization snapshots, bounded retention and isolated restores."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib,json,os,re,shutil,sqlite3,time,uuid
from pathlib import Path
from ..research.catalog import canonical,digest
from ..research.jobs import CURRENT_BUILD,open_job,metadata,save_meta
from .auth import Identity

ROOT=Path(__file__).resolve().parents[2]
NAME=re.compile(r"^team-\d{8}T\d{6}Z-[a-f0-9]{8}$")

def file_hash(path):
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()

def private_copy(source,target):
    if source.is_symlink() or not source.is_file():raise ValueError("Snapshot source must be a regular file")
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(target,"xb",opener=lambda path,flags:os.open(path,flags,0o600)) as out, source.open("rb") as inp:
        shutil.copyfileobj(inp,out)

def _source_archive(destination,code_hash,organization_root=None):
    if code_hash==CURRENT_BUILD["code_hash"]:
        files=[]
        for directory in ("app","static","tools","tests","deploy"):
            files.extend(p for p in (ROOT/directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts and (directory in {"static","deploy"} or p.suffix==".py"))
        files.extend(ROOT/name for name in ("run.py","requirements.txt","requirements.lock","requirements-dev.txt","requirements-dev.lock","pytest.ini"))
        for source in files:private_copy(source,destination/source.relative_to(ROOT))
        check=hashlib.sha256()
        for source in sorted((destination/"app").rglob("*.py")):check.update(str(source.relative_to(destination/"app")).encode());check.update(source.read_bytes())
        if check.hexdigest()!=code_hash:raise ValueError("Source changed during organization snapshot")
    else:
        local=Path(organization_root)/"releases"/code_hash if organization_root else None
        source=local if local and local.is_dir() else ROOT/"data/releases"/code_hash
        manifest=json.loads((source/"source-manifest.json").read_text())
        for name,expected in manifest.items():
            relative=Path(name)
            allowed=relative.parts[0] in {"app","static","tools","tests","deploy"} or name in {"run.py","requirements.txt","requirements.lock","requirements-dev.txt","requirements-dev.lock","pytest.ini"}
            if not allowed or relative.is_absolute() or ".." in relative.parts or any(part.startswith(".env") for part in relative.parts) or file_hash(source/relative)!=expected:raise ValueError("Archived source integrity failed")
            private_copy(source/relative,destination/relative)

def check_snapshot(directory):
    from tools.forward_ops import check_database
    directory=Path(directory).resolve();manifest=json.loads((directory/"manifest.json").read_text())
    if manifest["format"]!="artha-team-backup/1.0":raise ValueError("Unsupported organization snapshot")
    names=[item["path"] for item in manifest["files"]]
    if len(set(names))!=len(names) or not {"identity.sqlite3","workspace.sqlite3"}<=set(names) or not re.fullmatch(r"[a-f0-9]{32}",manifest["organization_id"]):
        raise ValueError("Organization snapshot inventory is incomplete")
    for item in manifest["files"]:
        relative=Path(item["path"])
        path=(directory/relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(directory) or (directory/relative).is_symlink() or not path.is_file() or file_hash(path)!=item["sha256"]:raise ValueError("Organization snapshot integrity failed")
        if path.suffix==".sqlite3":check_database(path)
    with closing(sqlite3.connect((directory/"identity.sqlite3").as_uri()+"?mode=ro",uri=True)) as db:
        org=json.loads(db.execute("SELECT document FROM team_identity_settings WHERE name='organization'").fetchone()[0])
        if org["id"]!=manifest["organization_id"] or db.execute("SELECT count(*) FROM team_identity_sessions").fetchone()[0] or db.execute("SELECT count(*) FROM team_identity_invites").fetchone()[0]:
            raise ValueError("Organization snapshot identity/session boundary failed")
    for code_hash in manifest["builds"]:
        if not re.fullmatch(r"[a-f0-9]{64}",code_hash):raise ValueError("Invalid source identity")
        archive=directory/"releases"/code_hash/"app";check=hashlib.sha256()
        for path in sorted(archive.rglob("*.py")):check.update(str(path.relative_to(archive)).encode());check.update(path.read_bytes())
        if check.hexdigest()!=code_hash:raise ValueError("Organization source archive identity failed")
    return dict(ok=True,files_verified=len(manifest["files"]),organization_id=manifest["organization_id"],plaintext_credentials_included=False,
        password_verifiers_included=True,sessions_included=False,production_data_replaced=False)

def prune(directory,organization_id,keep=7):
    if not 2<=keep<=100:raise ValueError("Retention must keep between 2 and 100 verified snapshots")
    candidates=[]
    for path in Path(directory).iterdir():
        if path.is_symlink() or not path.is_dir() or not NAME.fullmatch(path.name):continue
        try:
            manifest=json.loads((path/"manifest.json").read_text())
            if manifest["organization_id"]==organization_id and check_snapshot(path)["ok"]:candidates.append(path)
        except (OSError,ValueError,KeyError,sqlite3.Error):continue
    removed=[]
    for path in sorted(candidates,reverse=True)[keep:]:
        shutil.rmtree(path);removed.append(path.name)
    return removed

def snapshot(team,keep=7):
    from tools.backup import backup
    with team.lock:
        org=team.identity.organization()
        if not org:raise ValueError("Initialize an organization before taking its snapshot")
        team.workspace
        for run in team.workspace.research.runs():
            if run["status"] in {"QUEUED","RUNNING"}:team.workspace.research.control(run["id"],"PAUSE")
        deadline=time.monotonic()+15
        while any(r["status"] in {"QUEUED","RUNNING"} for r in team.workspace.research.runs()):
            if time.monotonic()>=deadline:raise ValueError("Wait for research workers to pause before taking a consistent snapshot")
            time.sleep(.1)
        destination=team.root/"backups";destination.mkdir(exist_ok=True,mode=0o700)
        name="team-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
        staging=destination/(name+".incomplete");staging.mkdir(mode=0o700)
        sources=[team.root/"identity.sqlite3",team.root/"workspace.sqlite3"]
        sources.extend((team.root/"research").rglob("*.sqlite3"))
        for source in sources:
            target=staging/source.relative_to(team.root);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);backup(source,target)
        with closing(sqlite3.connect(staging/"identity.sqlite3")) as db:
            with db:
                db.execute("DELETE FROM team_identity_sessions")
                db.execute("DELETE FROM team_identity_invites")
                db.execute("DELETE FROM team_identity_limits")
        for source in (team.root/"research/datasets").rglob("*"):
            if source.is_file():private_copy(source,staging/source.relative_to(team.root))
        hashes={CURRENT_BUILD["code_hash"]}
        for raw, in team.workspace.db.execute("SELECT document FROM mvp_protocols"):hashes.add(json.loads(raw)["core"]["build"]["code_hash"])
        for code_hash in hashes:_source_archive(staging/"releases"/code_hash,code_hash,team.root)
        files=[dict(path=str(p.relative_to(staging)),sha256=file_hash(p)) for p in sorted(staging.rglob("*")) if p.is_file()]
        manifest=dict(format="artha-team-backup/1.0",organization_id=org["id"],created_at=datetime.now(timezone.utc).isoformat(),
            files=files,builds=sorted(hashes),plaintext_credentials_included=False,password_verifiers_included=True,
            sessions_included=False,invitations_included=False,consistency="Organization mutations locked and research workers paused at committed checkpoints")
        (staging/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n");(staging/"manifest.json").chmod(0o600)
        verified=check_snapshot(staging)
        final=destination/name;staging.rename(final)
        removed=prune(destination,org["id"],keep)
        return dict(name=name,path=str(final),verified=verified,removed_managed_snapshots=removed,research_left_paused=True)

def list_snapshots(root):
    result=[]
    directory=Path(root)/"backups"
    if not directory.exists():return result
    for path in sorted(directory.iterdir(),reverse=True):
        if path.is_dir() and not path.is_symlink() and NAME.fullmatch(path.name):
            manifest=json.loads((path/"manifest.json").read_text())
            result.append(dict(name=path.name,created_at=manifest["created_at"],organization_id=manifest["organization_id"]))
    return result

def restore(directory,destination):
    directory=Path(directory).resolve();destination=Path(destination).resolve()
    check_snapshot(directory)
    if destination.exists() or destination.is_relative_to(directory):raise ValueError("Restore requires a new isolated destination")
    manifest=json.loads((directory/"manifest.json").read_text())
    destination.mkdir(parents=True,mode=0o700)
    for item in manifest["files"]:
        private_copy(directory/item["path"],destination/item["path"])
    identity=Identity(destination)
    try:
        with identity.lock,identity.db:
            org=identity.organization();org["generation"]+=1
            identity.db.execute("UPDATE team_identity_settings SET document=? WHERE name='organization'",(canonical(org),))
            identity.db.execute("DELETE FROM team_identity_sessions");identity.db.execute("DELETE FROM team_identity_invites")
            identity._event("ISOLATED_RESTORE","LOCAL_FILESYSTEM_OPERATOR",org["id"],dict(organization_hash=digest(org),scope_renewal_required=True))
    finally:identity.close()
    for path in (destination/"research/runs").glob("*/run.sqlite3"):
        with closing(open_job(path)) as db:
            meta=metadata(db)
            if meta["status"] in {"RUNNING","QUEUED"}:
                meta.update(status="PAUSED",error="Isolated restore requires operator review")
                with db:save_meta(db,meta);db.execute("INSERT OR REPLACE INTO control VALUES(1,'PAUSE')")
    return dict(ok=True,organization_id=manifest["organization_id"],restored_to=str(destination),sessions_revoked=True,
        previous_scopes_require_renewal=True,production_data_replaced=False)
