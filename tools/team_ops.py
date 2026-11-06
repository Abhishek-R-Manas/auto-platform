"""Verified organization backups and encrypted, pinned-host SSH delivery. No broker keys."""
import argparse,json,os,re,subprocess,sys,tarfile,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.team.service import Team
from app.team.recovery import snapshot,check_snapshot,restore,file_hash,_source_archive

def encrypted_archive(directory,output,recipient):
    if not re.fullmatch(r"[A-Fa-f0-9]{40}",recipient):raise ValueError("Use the exact 40-character GPG recipient fingerprint")
    directory=Path(directory);check_snapshot(directory);output=Path(output)
    if output.exists():raise ValueError("Encrypted output must be new")
    with tempfile.TemporaryDirectory(prefix="artha-private-archive-",dir=output.parent) as tmp:
        tar=Path(tmp)/"snapshot.tar"
        manifest=json.loads((directory/"manifest.json").read_text())
        names=["manifest.json"]+[item["path"] for item in manifest["files"]]
        with open(tar,"xb",opener=lambda path,flags:os.open(path,flags,0o600)) as stream,tarfile.open(fileobj=stream,mode="w") as archive:
            for name in names:archive.add(directory/name,arcname=name,recursive=False)
        result=subprocess.run(["gpg","--no-options","--batch","--no-auto-key-retrieve","--auto-key-locate","clear","--trust-model","always","--recipient",recipient,"--output",str(output),"--encrypt",str(tar)],capture_output=True,timeout=120)
        if result.returncode:
            output.unlink(missing_ok=True);raise ValueError("Backup encryption failed; verify the operator's pinned recipient key")
    output.chmod(0o600)
    return dict(path=str(output),sha256=file_hash(output),encrypted=True)

def publish(directory,host,remote_directory,recipient,keep=7):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}",host):raise ValueError("Use a configured SSH host alias")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{2,200}",remote_directory) or ".." in Path(remote_directory).parts or remote_directory in {"/","/root","/home","/tmp"}:raise ValueError("Use a dedicated absolute backup directory")
    if not 2<=keep<=100:raise ValueError("Retention must keep between 2 and 100 archives")
    directory=Path(directory);checked=check_snapshot(directory)
    org=checked["organization_id"]
    name=directory.name
    if not re.fullmatch(r"team-\d{8}T\d{6}Z-[a-f0-9]{8}",name):raise ValueError("Use a managed verified organization snapshot")
    remote=remote_directory.rstrip("/")+"/artha-"+org
    with tempfile.TemporaryDirectory(prefix="artha-team-send-",dir=directory.parent) as tmp:
        tmp=Path(tmp);encrypted=tmp/(name+".tar.gpg")
        encrypted_archive(directory,encrypted,recipient)
        readback=tmp/"verified-readback.gpg"
        def sftp(lines):
            batch=tmp/"transfer.sftp";batch.write_text("\n".join(lines)+"\n");batch.chmod(0o600)
            result=subprocess.run(["sftp","-b",str(batch),"-oBatchMode=yes","-oStrictHostKeyChecking=yes","-oIdentitiesOnly=yes","-oConnectTimeout=10","-oConnectionAttempts=1",host],capture_output=True,text=True,timeout=120)
            if result.returncode:raise ValueError("SSH backup transfer failed; verify the approved target, key and pinned host")
            return result.stdout
        def quoted(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'
        output=sftp(["-mkdir "+remote,"chmod 700 "+remote,"put "+quoted(encrypted)+" "+remote+"/"+encrypted.name+".partial",
            "chmod 600 "+remote+"/"+encrypted.name+".partial","rename "+remote+"/"+encrypted.name+".partial "+remote+"/"+encrypted.name,
            "get "+remote+"/"+encrypted.name+" "+quoted(readback),"ls -1 "+remote])
        if file_hash(readback)!=file_hash(encrypted):raise ValueError("Off-host backup readback hash mismatch")
        names=sorted({line.strip().rsplit("/",1)[-1] for line in output.splitlines() if re.fullmatch(r"team-\d{8}T\d{6}Z-[a-f0-9]{8}\.tar\.gpg",line.strip().rsplit("/",1)[-1])},reverse=True)
        old=names[keep:]
        if old:sftp(["rm "+remote+"/"+name for name in old])
        receipt=dict(checked_at=datetime.now(timezone.utc).isoformat(),organization_id=org,snapshot=name,encrypted=True,recipient_fingerprint=recipient.upper(),ssh_host=host,
            readback_hash_verified=True,sha256=file_hash(encrypted),remote_retention=keep,removed_managed_archives=len(old))
        receipts=directory.parent/"receipts";receipts.mkdir(exist_ok=True,mode=0o700)
        path=receipts/(name+".json");path.write_text(json.dumps(receipt,indent=2)+"\n");path.chmod(0o600)
        return receipt

def restore_archive(archive,destination):
    archive=Path(archive).resolve()
    with tempfile.TemporaryDirectory(prefix="artha-decrypt-") as tmp:
        tmp=Path(tmp);tar=tmp/"snapshot.tar";stage=tmp/"verified"
        with open(tar,"xb",opener=lambda path,flags:os.open(path,flags,0o600)) as stream:
            result=subprocess.run(["gpg","--no-options","--batch","--no-auto-key-retrieve","--max-output","2147483648","--decrypt",str(archive)],stdout=stream,stderr=subprocess.PIPE,timeout=120)
        if result.returncode:raise ValueError("Backup decryption failed; unlock the operator's matching private key")
        stage.mkdir(mode=0o700)
        with tarfile.open(tar,"r") as bundle:
            members=bundle.getmembers()
            if len(members)>20000 or sum(item.size for item in members)>2*1024*1024*1024 or any(not item.isfile() or item.name.startswith("/") or ".." in Path(item.name).parts for item in members):
                raise ValueError("Unsupported encrypted backup contents")
            bundle.extractall(stage,filter="data")
        check_snapshot(stage)
        return restore(stage,destination)

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest="command",required=True)
    saved=sub.add_parser("backup");saved.add_argument("--root",type=Path,required=True);saved.add_argument("--keep",type=int,default=7)
    combined=sub.add_parser("backup-publish");combined.add_argument("--root",type=Path,required=True);combined.add_argument("--host",required=True);combined.add_argument("--directory",required=True);combined.add_argument("--recipient",required=True);combined.add_argument("--keep",type=int,default=7)
    archived=sub.add_parser("archive-source");archived.add_argument("--root",type=Path,required=True)
    checked=sub.add_parser("verify");checked.add_argument("snapshot",type=Path)
    restored=sub.add_parser("restore");restored.add_argument("snapshot",type=Path);restored.add_argument("--destination",type=Path,required=True)
    decrypted=sub.add_parser("restore-archive");decrypted.add_argument("archive",type=Path);decrypted.add_argument("--destination",type=Path,required=True)
    sent=sub.add_parser("publish");sent.add_argument("snapshot",type=Path);sent.add_argument("--host",required=True);sent.add_argument("--directory",required=True);sent.add_argument("--recipient",required=True);sent.add_argument("--keep",type=int,default=7)
    args=parser.parse_args();os.umask(0o077)
    if args.command in {"backup","backup-publish"}:
        team=Team(args.root)
        try:result=snapshot(team,args.keep)
        finally:team.close()
        if args.command=="backup-publish":result=dict(snapshot=result,delivery=publish(Path(result["path"]),args.host,args.directory,args.recipient,args.keep))
    elif args.command=="archive-source":
        from app.research.jobs import CURRENT_BUILD
        team=Team(args.root)
        try:
            with team.lock:
                destination=team.root/"releases"/CURRENT_BUILD["code_hash"]
                if destination.exists():raise ValueError("This source archive already exists; it is immutable")
                _source_archive(destination,CURRENT_BUILD["code_hash"],team.root)
                result=dict(source_archive=str(destination),build=CURRENT_BUILD,plaintext_credentials_included=False)
        finally:team.close()
    elif args.command=="verify":result=check_snapshot(args.snapshot)
    elif args.command=="restore":result=restore(args.snapshot,args.destination)
    elif args.command=="restore-archive":result=restore_archive(args.archive,args.destination)
    else:result=publish(args.snapshot,args.host,args.directory,args.recipient,args.keep)
    print(json.dumps(result,indent=2))
if __name__=="__main__":
    try:main()
    except (OSError,ValueError,subprocess.SubprocessError) as error:raise SystemExit(str(error) if isinstance(error,ValueError) else "Operation failed; inspect private operator configuration") from None
