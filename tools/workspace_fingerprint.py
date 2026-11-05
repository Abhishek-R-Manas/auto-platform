"""Content identity for code, browser assets, checks and dependency locks."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def fingerprints():
    paths=[]
    for directory in ('app','static','tools','tests','deploy'):
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and (directory in {'static','deploy'} or p.suffix=='.py'))
    paths.extend(ROOT/name for name in ('run.py','requirements.lock','requirements-dev.lock','requirements.txt','requirements-dev.txt','pytest.ini'))
    files={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return dict(sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),files=files)
