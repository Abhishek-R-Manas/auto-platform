"""Archive and verify application source/locks/assets; never include credentials or databases."""
from pathlib import Path
import hashlib,json,shutil,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.research.jobs import CURRENT_BUILD

def archive():
    destination=ROOT/'data/releases'/CURRENT_BUILD['code_hash'];destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    files=list((ROOT/'app').rglob('*.py'))+list((ROOT/'static').rglob('*'))+list((ROOT/'tools').glob('*.py'))+list((ROOT/'deploy').glob('*'))+list((ROOT/'tests').glob('*.py'))+[ROOT/name for name in ('run.py','requirements.txt','requirements.lock','requirements-dev.lock','requirements-dev.txt','pytest.ini')]
    manifest={}
    for source in files:
        if not source.is_file():continue
        relative=source.relative_to(ROOT);target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=source.read_bytes():raise ValueError('Existing source archive differs; refusing overwrite')
        if not target.exists():shutil.copy2(source,target)
        manifest[str(relative)]=hashlib.sha256(target.read_bytes()).hexdigest()
    code=hashlib.sha256()
    for source in sorted((destination/'app').rglob('*.py')):code.update(str(source.relative_to(destination/'app')).encode());code.update(source.read_bytes())
    if code.hexdigest()!=CURRENT_BUILD['code_hash']:raise ValueError('Archive code hash verification failed')
    (destination/'build.json').write_text(json.dumps(CURRENT_BUILD,indent=2)+'\n')
    (destination/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(dict(source_archive=str(destination),build=CURRENT_BUILD,verified_files=len(manifest),credentials_included=False)))

if __name__=='__main__':archive()
