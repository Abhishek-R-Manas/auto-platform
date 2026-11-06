"""Private backup/restore and encrypted delivery through a controlled SSH transport."""
import json,os,shlex,shutil,subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from app.team.recovery import snapshot,check_snapshot,restore,prune
from app.team.service import Team
from app.team.gates import authority_reason
from app.research.jobs import execute
from test_team import team,evidence,scope,PASSWORD,EMAIL
from tools.team_ops import publish,restore_archive

def test_backup_restore_preserves_evidence_and_requires_new_authority(team,tmp_path):
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet)
    token,csrf,_=service.identity.login(EMAIL,PASSWORD,"fixture")
    saved=snapshot(service)
    directory=Path(saved["path"]);assert check_snapshot(directory)["ok"]
    assert not (directory/"bootstrap.key").exists()
    target=tmp_path/"restored";result=restore(directory,target)
    assert result["sessions_revoked"] and result["previous_scopes_require_renewal"]
    restored=Team(target)
    try:
        assert restored.protocol(p["id"])==p
        assert restored.packet(packet["id"])["core"]["packet"]==service.packet(packet["id"])["core"]["packet"]
        assert restored.identity.organization()["generation"]==service.identity.organization()["generation"]+1
        with pytest.raises(HTTPException):restored.identity.authenticate(token)
        auth=dict(organization_id=service.identity.organization()["id"],generation=1,actor_id=author["id"],actor_version=author["version"],operation_id=None,grant_id=grant["id"],protocol_id=p["id"],scenario="BASELINE")
        assert "renewed" in authority_reason(target,auth)
        assert restored.identity.login(EMAIL,PASSWORD,"restore-fixture")[2]["id"]==author["id"]
    finally:restored.close()
    with pytest.raises(ValueError,match="new isolated"):restore(directory,service.root)

def test_retention_only_removes_managed_verified_snapshots(team,tmp_path):
    service,author,reviewer=team;evidence(service,author)
    first=Path(snapshot(service)["path"]);backups=first.parent
    for n in (1,2):
        shutil.copytree(first,backups/("team-2026010"+str(n)+"T000000Z-"+str(n)*8))
    unrelated=backups/"operator-files";unrelated.mkdir();(unrelated/"keep").write_text("preserve")
    incomplete=backups/"team-20250101T000000Z-00000000";incomplete.mkdir()
    removed=prune(backups,service.identity.organization()["id"],2)
    assert len(removed)==1 and (unrelated/"keep").exists() and incomplete.exists()
    with pytest.raises(ValueError):prune(backups,service.identity.organization()["id"],0)

def test_paused_research_survives_isolated_restore_without_autoresume(team,tmp_path,monkeypatch):
    from app.research.contract_replay import ContractReplay
    from app.research.jobs import open_job
    from contextlib import closing
    service,author,reviewer=team;p,packet=evidence(service,author);grant=scope(service,reviewer,p,packet)
    item=service.start(author,dict(request_id="pause-for-restore",protocol_id=p["id"],kind="BASELINE",grant_id=grant["id"]))
    original=ContractReplay.step
    def pause_after_entry(replay,rows,cursor,**kwargs):
        original(replay,rows,cursor,**kwargs)
        if cursor==3:
            with closing(open_job(service.workspace.research.path(item["run_id"]))) as db:
                with db:db.execute("INSERT OR REPLACE INTO control VALUES(1,'PAUSE')")
    monkeypatch.setattr(ContractReplay,"step",pause_after_entry)
    execute(service.workspace.research.root,item["run_id"])
    assert service.experiments.trial(item["id"])["status"]=="PAUSED"
    saved=snapshot(service);target=tmp_path/"restored-paused";restore(saved["path"],target)
    restored=Team(target)
    try:
        restored.workspace
        assert restored.experiments.trial(item["id"])["status"]=="PAUSED"
        with pytest.raises(HTTPException):restored.control(author,item["id"],"RESUME")
        assert len(restored.workspace.research.events(item["run_id"],kind="FILL"))==2
    finally:restored.close()

def test_encrypted_offhost_copy_readback_and_decrypted_restore(team,tmp_path,monkeypatch):
    service,author,reviewer=team;p,packet=evidence(service,author)
    saved=Path(snapshot(service)["path"])
    keyring=tmp_path/"gpg";keyring.mkdir(mode=0o700);monkeypatch.setenv("GNUPGHOME",str(keyring))
    generated=subprocess.run(["gpg","--batch","--pinentry-mode","loopback","--passphrase","","--quick-generate-key","Controlled backup <fixture@example.test>","rsa2048","encr","1d"],capture_output=True,timeout=60)
    assert generated.returncode==0
    keys=subprocess.run(["gpg","--batch","--with-colons","--list-keys"],capture_output=True,text=True,check=True)
    fingerprint=next(line.split(":")[9] for line in keys.stdout.splitlines() if line.startswith("fpr:"))
    real_run=subprocess.run;remote=tmp_path/"controlled-remote";remote.mkdir();calls=[]
    def controlled_run(argv,**kwargs):
        if argv[0]!="sftp":return real_run(argv,**kwargs)
        calls.append(argv);commands=Path(argv[argv.index("-b")+1]).read_text().splitlines();listing=""
        def path(value):return remote/value.lstrip("/")
        for line in commands:
            fields=shlex.split(line);command=fields[0]
            if command=="-mkdir":path(fields[1]).mkdir(parents=True,exist_ok=True)
            elif command=="chmod":pass
            elif command=="put":shutil.copyfile(fields[1],path(fields[2]))
            elif command=="get":shutil.copyfile(path(fields[1]),fields[2])
            elif command=="rename":path(fields[1]).rename(path(fields[2]))
            elif command=="ls":listing="\n".join(p.name for p in path(fields[-1]).iterdir())
            elif command=="rm":path(fields[1]).unlink()
            else:raise AssertionError(command)
        return subprocess.CompletedProcess(argv,0,listing,"")
    monkeypatch.setattr(subprocess,"run",controlled_run)
    receipt=publish(saved,"controlled-target","/approved/backups",fingerprint,2)
    assert receipt["encrypted"] and receipt["readback_hash_verified"] and calls
    assert all("-oStrictHostKeyChecking=yes" in args and "-oBatchMode=yes" in args for args in calls)
    encrypted=next(remote.rglob("*.tar.gpg"))
    assert b"sqlite" not in encrypted.read_bytes()[:50]
    target=tmp_path/"decrypted-restored"
    assert restore_archive(encrypted,target)["ok"]
    restored=Team(target)
    try:
        assert restored.protocol(p["id"])==p
        assert restored.identity.organization()["generation"]==2
    finally:restored.close()
    with pytest.raises(ValueError):publish(saved,"-oProxyCommand=bad","/approved/backups",fingerprint,2)
    with pytest.raises(ValueError):publish(saved,"controlled-target","/approved/../escape",fingerprint,2)
