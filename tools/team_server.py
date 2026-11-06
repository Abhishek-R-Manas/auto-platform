"""Run an organization-only listener with an explicit isolated store."""
import argparse,fcntl,os,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def main():
    import uvicorn
    from app.team.api import create_team_app
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--root",type=Path,required=True);parser.add_argument("--port",type=int,default=8788);parser.add_argument("--public-host",action="append",default=[])
    args=parser.parse_args();os.umask(0o077)
    if not 1024<=args.port<=65535:raise ValueError("Use a valid local port")
    if any(not re.fullmatch(r"[A-Za-z0-9.-]{1,253}",host) for host in args.public_host):raise ValueError("Use explicit trusted public hostnames")
    args.root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (args.root/"server.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        app=create_team_app(args.root,hosts=["127.0.0.1","localhost",*args.public_host])
        uvicorn.run(app,host="127.0.0.1",port=args.port,workers=1,access_log=False,proxy_headers=True,forwarded_allow_ips="127.0.0.1",timeout_graceful_shutdown=8)
if __name__=="__main__":main()
