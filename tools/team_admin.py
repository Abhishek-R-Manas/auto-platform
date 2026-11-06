"""Local operator setup/recovery. Passwords use private terminal prompts, never argv."""
import argparse,getpass,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.team.auth import Identity
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("command",choices=["setup","recover-admin"]);parser.add_argument("--root",type=Path,required=True);parser.add_argument("--email",required=True);parser.add_argument("--organization")
    args=parser.parse_args();os.umask(0o077);identity=Identity(args.root)
    try:
        password=getpass.getpass("New password (at least 15 characters): ")
        if password!=getpass.getpass("Repeat password: "):raise ValueError("Passwords did not match")
        if args.command=="setup":
            if not args.organization:raise ValueError("Supply --organization for initial setup")
            key=(identity.root/"bootstrap.key").read_text()
            result=identity.bootstrap(key,args.organization,args.email,password)
        else:result=identity.recover_admin(args.email,password)
        print(json.dumps(dict(member_id=result["id"],initialized=True,password_returned=False)))
    finally:identity.close()
if __name__=="__main__":main()
