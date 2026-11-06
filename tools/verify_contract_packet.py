"""Offline JSON evidence verification; no strategy execution or network access."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.contract_research import verify_packet,MAX_PACKET_BYTES

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("packet",type=Path)
    parser.add_argument("--expected-protocol")
    parser.add_argument("--expected-build")
    parser.add_argument("--expected-dataset")
    parser.add_argument("--require-reproduction",action="store_true")
    args=parser.parse_args()
    try:
        if args.packet.stat().st_size>MAX_PACKET_BYTES:raise ValueError("Oversize packet")
        result=verify_packet(json.loads(args.packet.read_text()),args.expected_protocol,args.expected_build,args.expected_dataset)
    except (OSError,UnicodeError,ValueError,RecursionError):
        result=dict(integrity_valid=False,reason="Packet cannot be read or exceeds the supported format")
    print(json.dumps(result,indent=2))
    return 0 if result["integrity_valid"] and (not args.require_reproduction or result.get("repeat_results_match")) else 1

if __name__=="__main__":raise SystemExit(main())
