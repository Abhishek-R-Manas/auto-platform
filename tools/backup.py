"""Create a private, consistent SQLite backup, including committed WAL data."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Backup must use a separate destination")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)) as live:
            with closing(sqlite3.connect(destination)) as saved:
                live.backup(saved)
                if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
        return destination
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination",type=Path)
    parser.add_argument("--source",type=Path,default=Path(__file__).resolve().parents[1]/"data/paper.sqlite3")
    args=parser.parse_args()
    print(backup(args.source,args.destination))
