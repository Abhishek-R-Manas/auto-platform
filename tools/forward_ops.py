"""Bounded local operations: health, online database snapshots and isolated restore checks."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.request import build_opener,ProxyHandler

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.backup import backup
from app.research.catalog import canonical,digest


def check_database(path):
    result=dict(file=path.name)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity failed')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {'portfolios','events'}<=tables:
            total=0
            for identifier,document,state,state_hash in db.execute('SELECT id,document,state,state_hash FROM portfolios'):
                doc=json.loads(document);expected=doc.pop('document_hash',None)
                if digest(doc)!=expected or digest(json.loads(state))!=state_hash:raise ValueError('Forward protocol/checkpoint hash failed')
                previous='0'*64;sequence=0;review_count=0
                for (value,) in db.execute('SELECT document FROM events WHERE portfolio_id=? ORDER BY sequence',(identifier,)):
                    item=json.loads(value);expected=item.pop('hash',None);sequence+=1
                    if item['sequence']!=sequence or item['previous_hash']!=previous or digest(item)!=expected:raise ValueError('Forward event chain failed')
                    previous=expected
                    if item['kind']=='GUARDIAN_REVIEW':
                        review_count+=1
                        saved=db.execute('SELECT document FROM reviews WHERE id=? AND portfolio_id=?',(item['review']['id'],identifier)).fetchone()
                        if not saved or json.loads(saved[0])!=item['review']:raise ValueError('Guardian review backup integrity failed')
                if 'reviews' in tables and db.execute('SELECT count(*) FROM reviews WHERE portfolio_id=?',(identifier,)).fetchone()[0]!=review_count:raise ValueError('Guardian review backup event count mismatch')
                if json.loads(state)['sequence']!=sequence:raise ValueError('Forward event/checkpoint sequence mismatch')
                total+=sequence
            result['forward_events_verified']=total
        if {'artifacts','observations','input_events'}<=tables:
            previous='0'*64;sequence=0;observations=0
            for (value,) in db.execute('SELECT document FROM input_events ORDER BY sequence'):
                event=json.loads(value);expected=event.pop('hash',None);sequence+=1
                if event['sequence']!=sequence or event['previous_hash']!=previous or digest(event)!=expected:raise ValueError('Input evidence chain failed')
                row=db.execute('SELECT document FROM artifacts WHERE id=?',(event['artifact_id'],)).fetchone()
                if not row:raise ValueError('Input artifact missing from backup')
                artifact=json.loads(row[0]);check=dict(artifact);hashed=check.pop('artifact_hash',None)
                if digest(check)!=hashed or event['artifact_hash']!=hashed or hashlib.sha256(artifact['content'].encode()).hexdigest()!=artifact['raw_sha256']:raise ValueError('Input artifact backup hash failed')
                for observation in artifact['observations']:
                    stored=db.execute('SELECT document FROM observations WHERE id=?',(observation['id'],)).fetchone()
                    if not stored or json.loads(stored[0])!=observation:raise ValueError('Input observation backup integrity failed')
                observations+=len(artifact['observations']);previous=expected
            if db.execute('SELECT count(*) FROM artifacts').fetchone()[0]!=sequence or db.execute('SELECT count(*) FROM observations').fetchone()[0]!=observations:raise ValueError('Input backup evidence counts failed')
            result['input_artifacts_verified']=sequence;result['input_observations_verified']=observations
        if {'settings','intents','previews','events'}<=tables:
            previous='0'*64;sequence=0;latest={};mandate=None;snapshot_hash=None
            for row in db.execute('SELECT sequence,document FROM events ORDER BY sequence'):
                event=json.loads(row[1]);expected=event.pop('hash');sequence+=1
                if event['sequence']!=sequence or row[0]!=sequence or event['previous_hash']!=previous or digest(event)!=expected:raise ValueError('Pilot event chain failed')
                previous=expected
                if event['kind']=='MANDATE':mandate=event['details']
                if event['kind']=='RECONCILIATION':snapshot_hash=event['details']['hash']
                if event['kind'] in {'SEND_RESERVED','BROKER_ACK','SEND_UNCERTAIN','RECONCILED_ORDER','CANCEL_RESERVED','CANCEL_ACK','CANCEL_UNCERTAIN','RESTART_UNCERTAIN'}:latest[event['details']['id']]=event['details']
            actual={json.loads(row[0])['id']:json.loads(row[0]) for row in db.execute('SELECT document FROM intents')}
            if latest!=actual:raise ValueError('Pilot order/event backup mismatch')
            settings={row[0]:json.loads(row[1]) for row in db.execute('SELECT name,document FROM settings')}
            if settings.get('integrity_halt'):raise ValueError('Pilot evidence has a latched integrity halt')
            if settings.get('mandate')!=mandate:raise ValueError('Pilot mandate backup mismatch')
            if settings.get('snapshot') is not None and digest(settings['snapshot'])!=snapshot_hash:raise ValueError('Pilot broker snapshot backup mismatch')
            if settings.get('portfolio_snapshot') is not None and digest(settings['portfolio_snapshot'])!=snapshot_hash:raise ValueError('Portfolio display snapshot backup mismatch')
            result['pilot_events_verified']=sequence
        if {'drafts','chains','quote_cache'}<=tables:
            for identifier,value in db.execute('SELECT id,document FROM drafts'):
                item=json.loads(value)
                if item['id']!=identifier or digest(dict(basket=item['basket'],parent_id=item['parent_id']))!=identifier:raise ValueError('Workspace draft backup integrity failed')
            for identifier,value in db.execute('SELECT id,document FROM chains'):
                item=json.loads(value);expected=item.pop('id')
                if expected!=identifier or digest(item)!=identifier:raise ValueError('Workspace chain backup integrity failed')
            result['workspace_drafts_verified']=db.execute('SELECT count(*) FROM drafts').fetchone()[0]
            result['workspace_chains_verified']=db.execute('SELECT count(*) FROM chains').fetchone()[0]
        result['integrity']='ok' 
    return result


def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def snapshot(data_root,destination):
    if destination.exists():raise ValueError('Snapshot destination must be new')
    if destination.is_relative_to(data_root) and destination.relative_to(data_root).parts[0]!='backups':raise ValueError('An internal snapshot must be under data/backups')
    destination.mkdir(parents=True,mode=0o700)
    files=[]
    for source in sorted(data_root.rglob('*.sqlite3')):
        relative=source.relative_to(data_root)
        if any(p in {'releases','backups'} for p in relative.parts) or source.name.startswith('pre-'):continue
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        backup(source,target);files.append(dict(path=str(relative),sha256=file_hash(target),**check_database(target)))
    artifacts=[]
    for relative_root in ('research/datasets','releases'):
        directory=data_root/relative_root
        if not directory.exists():continue
        for source in sorted(directory.rglob('*')):
            if not source.is_file() or source.is_symlink() or '__pycache__' in source.parts or source.name.startswith('.env'):continue
            relative=source.relative_to(data_root);target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            __import__('shutil').copyfile(source,target);target.chmod(0o600)
            artifacts.append(dict(path=str(relative),sha256=file_hash(target)))
    manifest=dict(created_at=datetime.now(timezone.utc).isoformat(),files=files,artifacts=artifacts,secrets_included=False,
        consistency='Each SQLite backup is consistent. Pause mutation/jobs first when a cross-database instant is required.')
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');return manifest


def restore_check(directory):
    root=directory.resolve();manifest=json.loads((root/'manifest.json').read_text());checked=[]
    for item in manifest['files']:
        path=(root/item['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise ValueError('Invalid snapshot path')
        if file_hash(path)!=item['sha256']:raise ValueError('Snapshot hash mismatch')
        checked.append(check_database(path))
    for item in manifest.get('artifacts',[]):
        path=(root/item['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_hash(path)!=item['sha256']:raise ValueError('Immutable artifact backup hash mismatch')
    return dict(ok=True,checked=checked,artifacts_verified=len(manifest.get('artifacts',[])),production_data_replaced=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    health=sub.add_parser('health');health.add_argument('--port',type=int,default=8787)
    saved=sub.add_parser('backup');saved.add_argument('--data',type=Path,default=ROOT/'data');saved.add_argument('--destination',type=Path)
    restored=sub.add_parser('restore-check');restored.add_argument('directory',type=Path)
    args=parser.parse_args();os.umask(0o077)
    if args.command=='health':
        if not 1024<=args.port<=65535:raise ValueError('Port outside supported local range')
        opener=build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:'+str(args.port)+'/api/health',timeout=10) as response:result=json.load(response)
        print(json.dumps(result));return 0 if result['status']=='ok' else 1
    if args.command=='backup':
        destination=args.destination or ROOT/'data/backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        result=snapshot(args.data.resolve(),destination.resolve())
        print(json.dumps(dict(destination=str(destination),databases=len(result['files']),secrets_included=False)))
    else:print(json.dumps(restore_check(args.directory)))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,sqlite3.Error):
        print('Operation failed; verify local paths, integrity, permissions and service health. No credentials were printed.',file=sys.stderr);raise SystemExit(1)
