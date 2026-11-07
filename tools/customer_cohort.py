"""Aggregate downloaded administrator reports from distinct, explicitly selected organizations."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.team.customer_cohort import aggregate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports",nargs="+",type=Path)
    args=parser.parse_args()
    if len(args.reports)>200:raise ValueError("Select at most 200 organization reports")
    reports=[]
    for path in args.reports:
        if path.stat().st_size>2*1024*1024:raise ValueError("Organization report exceeds 2 MB")
        reports.append(json.loads(path.read_text()))
    try:result=aggregate(reports)
    except (ValueError,KeyError,TypeError) as error:parser.error(str(error))
    print(json.dumps(result,indent=2))


if __name__=="__main__":main()
