"""Immutable, content-addressed datasets with explicit quarantine and provenance."""
import base64
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import Manifest, aware, iso

MAX_ROWS=250000
COLUMNS={"instrument_id":pa.string(),"timestamp":pa.string(),"open_at":pa.string(),"received_at":pa.string(),"available_at":pa.string(),"exchange_time":pa.string(),
    **{k:pa.float64() for k in ("open","high","low","close","bid","ask","underlying_price","iv","settlement","margin_per_lot","borrow_rate")},
    **{k:pa.int64() for k in ("volume","oi","bid_quantity","ask_quantity","borrow_quantity")},
    "halted":pa.bool_(),"features_json":pa.string()}
SCHEMA=pa.schema(list(COLUMNS.items()))


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def session_for(calendar, day):
    day=day.isoformat() if not isinstance(day,str) else day
    if day in calendar["special_sessions"]: return calendar["special_sessions"][day]
    if day in calendar["holidays"] or datetime.fromisoformat(day).weekday()>=5: return None
    return calendar["session"]


def bounds(calendar, day):
    session=session_for(calendar,day)
    if not session: return None
    zone=ZoneInfo(calendar["timezone"])
    return tuple(aware(datetime.fromisoformat(day.isoformat()+"T"+session[k]).replace(tzinfo=zone)) for k in ("open","close"))


def settlement_date(calendar, at):
    day=aware(at).astimezone(ZoneInfo(calendar["timezone"])).date()
    for _ in range(calendar["settlement_days"]):
        day+=timedelta(days=1)
        while not session_for(calendar,day): day+=timedelta(days=1)
    return day.isoformat()


def _number(row,key,default=None,integer=False):
    raw=row.get(key)
    if raw in (None,""): return default
    value=float(raw)
    if not math.isfinite(value) or (not integer and abs(value)>1000000000000): raise ValueError(f"{key} must be finite and within supported numeric bounds")
    if integer:
        if value<0 or value>9223372036854775807 or value!=int(value): raise ValueError(f"{key} must be a non-negative whole number")
        return int(value)
    return value


def normalize(row, manifest, instruments):
    identifier=row.get("instrument_id") or row.get("symbol")
    if identifier not in instruments:
        aliases=[i["id"] for i in instruments.values() if i["symbol"]==identifier]
        if len(aliases)!=1: raise ValueError("Unknown or ambiguous instrument_id")
        identifier=aliases[0]
    raw=row.get("timestamp") or row.get("date")
    if not raw: raise ValueError("Missing timestamp")
    zone=ZoneInfo(manifest["calendar"]["timezone"])
    if len(str(raw))==10 and manifest["frequency"]=="DAY":
        day=datetime.strptime(str(raw),"%Y-%m-%d").date()
        span=bounds(manifest["calendar"],day)
        if not span: raise ValueError("Observation falls on a closed session")
        at=span[0] if manifest["timestamp_semantics"]=="START" else span[1]
    else: at=aware(raw)
    day=at.astimezone(zone).date()
    span=bounds(manifest["calendar"],day)
    if not span: raise ValueError("Observation falls on a closed session")
    if manifest["frequency"]=="DAY":
        open_at=span[0]
        if manifest["timestamp_semantics"]=="START": at=span[1]
        if at!=span[1]: raise ValueError("Daily timestamps must denote session close (or open with START semantics)")
    elif manifest["frequency"]=="MINUTE":
        if manifest["timestamp_semantics"]=="START":
            open_at=at;at+=timedelta(minutes=manifest["interval_minutes"])
        else: open_at=at-timedelta(minutes=manifest["interval_minutes"])
        if open_at<span[0] or at>span[1]: raise ValueError("Bar extends outside the configured session")
    else:
        open_at=at
        if not span[0]<=at<=span[1]: raise ValueError("Quote outside the configured session")
    close=_number(row,"close",_number(row,"last"))
    item=instruments[identifier]
    expired_zero=item["kind"]=="OPT" and item["expiry"] and at>=aware(item["expiry"])
    if close is None or close<0 or (close==0 and not expired_zero): raise ValueError("Positive close/last required; zero is allowed for expired option settlement observations")
    result=dict(instrument_id=identifier,timestamp=iso(at),open_at=iso(open_at),close=close)
    received=row.get("received_at")
    available=row.get("available_at")
    if received and not available and row.get("exchange_time") and manifest.get("observation_clock","UNSPECIFIED")=="UNSPECIFIED":
        # Existing Captures CSV declares timestamp as availability, with a separate
        # exchange_time and epoch receipt. Preserve that clock instead of reinterpreting it.
        receipt=datetime.fromtimestamp(float(received),timezone.utc)
        exchange=aware(row["exchange_time"])
        if not exchange<=receipt<=at:raise ValueError("Legacy capture clocks must be causal")
        result.update(received_at=iso(receipt),available_at=iso(at),exchange_time=iso(exchange))
    elif received or available or manifest.get("observation_clock")=="EXCHANGE_AND_RECEIPT":
        if not received or not available:raise ValueError("Receipt-clock datasets require received_at and available_at")
        if not at<=aware(received)<=aware(available):raise ValueError("Exchange, receipt and availability clocks must be causal")
        result.update(received_at=iso(received),available_at=iso(available))
    for key in ("open","high","low"):
        result[key]=_number(row,key,close if manifest["frequency"]=="QUOTE" else None)
        if result[key] is None or result[key]<0 or (result[key]==0 and not expired_zero): raise ValueError(f"Positive {key} price required")
    if result["low"]>min(result["open"],close) or result["high"]<max(result["open"],close) or result["low"]>result["high"]:
        raise ValueError("OHLC prices are inconsistent")
    for key in ("volume","oi","bid_quantity","ask_quantity","borrow_quantity"):
        result[key]=_number(row,key,0 if key=="volume" else None,integer=True)
    for key in ("bid","ask","underlying_price","iv","settlement","margin_per_lot","borrow_rate"):
        result[key]=_number(row,key)
        if result[key] is not None and (result[key]<0 or (key not in {"borrow_rate","margin_per_lot"} and result[key]==0 and not expired_zero)):
            raise ValueError(f"Invalid {key}")
    if result["bid"] is not None and result["ask"] is not None and result["bid"]>result["ask"]: raise ValueError("Crossed bid/ask")
    if manifest["frequency"]=="QUOTE" and any(result[k] is None for k in ("bid","ask","bid_quantity","ask_quantity")):
        raise ValueError("Quote datasets require bid/ask and executable depth")
    result["halted"]=str(row.get("halted",False)).lower() in {"true","1"}
    features=row.get("features") or row.get("features_json") or {}
    if isinstance(features,str): features=json.loads(features)
    if not isinstance(features,dict) or len(features)>100: raise ValueError("Invalid feature map")
    for key,item in features.items():
        if not isinstance(item,dict) or "value" not in item or "available_at" not in item: raise ValueError("Features need value and available_at")
        if not isinstance(item["value"],(int,float)) or not math.isfinite(item["value"]): raise ValueError("Invalid feature value")
        if aware(item["available_at"])>at: raise ValueError("Feature was not available at the observation time")
    result["features_json"]=canonical(features)
    return result


class Catalog:
    def __init__(self,root):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.root/"catalog.sqlite3",check_same_thread=False,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,created TEXT NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,created TEXT NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY,created TEXT NOT NULL,document TEXT NOT NULL);""")

    def close(self): self.db.close()

    def get(self,identifier):
        with self.lock:
            row=self.db.execute("SELECT document FROM datasets WHERE id=?",(identifier,)).fetchone()
        if not row: raise ValueError("Dataset not found")
        return json.loads(row[0])

    def datasets(self):
        with self.lock: rows=self.db.execute("SELECT document FROM datasets ORDER BY created DESC LIMIT 500").fetchall()
        return [json.loads(r[0]) for r in rows]

    def import_data(self,body):
        manifest=body.manifest.model_dump(mode="json")
        if manifest["parent_dataset_id"]: self.get(manifest["parent_dataset_id"])
        if body.format=="CSV":
            raw=body.content.encode("utf-8")
            reader=csv.DictReader(io.StringIO(body.content.lstrip("\ufeff")))
            if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames): raise ValueError('CSV headers must be unique')
            records=[]
            for row in reader:
                if None in row: raise ValueError('CSV row has more cells than declared headers')
                if len(records)>=MAX_ROWS: raise ValueError(f"Dataset exceeds {MAX_ROWS} rows; split into immutable batches")
                records.append(row)
        else:
            try: raw=base64.b64decode(body.content,validate=True)
            except ValueError: raise ValueError("Parquet content must be base64 encoded") from None
            try:
                file=pq.ParquetFile(io.BytesIO(raw))
                if file.metadata.num_rows>MAX_ROWS or file.metadata.num_columns>150 or sum(file.metadata.row_group(i).total_byte_size for i in range(file.metadata.num_row_groups))>256000000: raise ValueError("Parquet dataset exceeds resource limits")
                records=file.read(use_threads=False).to_pylist()
            except (pa.ArrowException,OSError): raise ValueError("Invalid Parquet input") from None
        if not records: raise ValueError("Dataset has no observations")
        instruments={i["id"]:i for i in manifest["instruments"]}
        valid,errors,seen=[],[],set()
        invalid_count=0
        for number,row in enumerate(records,2):
            try:
                item=normalize(row,manifest,instruments)
                key=(item["timestamp"],item["instrument_id"])
                if key in seen: raise ValueError("Duplicate instrument/timestamp")
                seen.add(key);valid.append(item)
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                invalid_count+=1
                if len(errors)<100: errors.append(dict(row=number,reason=str(exc)))
        was_sorted=all(a["timestamp"]<=b["timestamp"] for a,b in zip(valid,valid[1:]))
        valid.sort(key=lambda x:(x["timestamp"],x["instrument_id"]))
        warnings=[]
        if not was_sorted: warnings.append("Input was reordered chronologically; raw order is preserved in the source file")
        if manifest["source_kind"]=="SYNTHETIC": warnings.append("SYNTHETIC TEST DATA: results do not establish historical market performance")
        if not manifest["rights_confirmed"]: warnings.append("Data usage rights have not been confirmed by the owner")
        if not manifest["point_in_time_universe"]: warnings.append("Historical universe membership is not established")
        if not manifest["corporate_actions_complete"]: warnings.append("Corporate-action completeness is not established")
        if not manifest["margin_history_complete"]: warnings.append("Historical margin coverage is not established")
        if manifest["prices_adjusted"]: warnings.append("Adjusted prices cannot represent raw executable prices; accounting qualification is blocked")
        if any("ILLUSTRATIVE" in f["source"].upper() for f in manifest["fee_rules"]): warnings.append("Charges include an illustrative profile; dated broker charges are required for qualification")
        if manifest["frequency"]!="QUOTE": warnings.append("Bar data does not establish within-bar sequence or historical bid/ask execution")
        coverage=[]
        if valid:
            start=aware(valid[0]["timestamp"]).astimezone(ZoneInfo(manifest["calendar"]["timezone"])).date()
            end=aware(valid[-1]["timestamp"]).astimezone(ZoneInfo(manifest["calendar"]["timezone"])).date()
            if (end-start).days>366*50: raise ValueError("One dataset revision may span at most 50 years")
            grouped={}
            for row in valid: grouped.setdefault(row["instrument_id"],[]).append(row)
            for identifier,item in instruments.items():
                rows=grouped.get(identifier,[])
                observed={aware(r["timestamp"]).astimezone(ZoneInfo(manifest["calendar"]["timezone"])).date() for r in rows}
                missing=[];day=start
                while day<=end:
                    span=bounds(manifest["calendar"],day)
                    active=span and (not item["listed_at"] or aware(item["listed_at"])<=span[1]) and (not item["delisted_at"] or aware(item["delisted_at"])>=span[0]) and (not item["expiry"] or aware(item["expiry"])>=span[0])
                    if active and day not in observed: missing.append(day.isoformat())
                    day+=timedelta(days=1)
                gaps=0
                if manifest["frequency"]=="MINUTE":
                    for a,b in zip(rows,rows[1:]):
                        if a["timestamp"][:10]==b["timestamp"][:10] and (aware(b["timestamp"])-aware(a["timestamp"])).total_seconds()>manifest["interval_minutes"]*60: gaps+=1
                coverage.append(dict(instrument_id=identifier,rows=len(rows),missing_sessions=len(missing),missing_sample=missing[:20],intraday_gap_intervals=gaps))
            if any(c["missing_sessions"] or c["intraday_gap_intervals"] for c in coverage): warnings.append("Session or intraday coverage gaps detected; inspect per-instrument coverage")
        raw_hash=hashlib.sha256(raw).hexdigest()
        identifier=digest(dict(manifest=manifest,raw_hash=raw_hash))
        with self.lock:
            existing=self.db.execute("SELECT document FROM datasets WHERE id=?",(identifier,)).fetchone()
            if existing: return json.loads(existing[0])
            directory=self.root/"datasets"/identifier
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            (directory/("raw.csv" if body.format=="CSV" else "raw.parquet")).write_bytes(raw)
            table=pa.Table.from_pylist(valid,schema=SCHEMA)
            pq.write_table(table,directory/"observations.parquet",compression="zstd")
            parquet_hash=hashlib.sha256((directory/"observations.parquet").read_bytes()).hexdigest()
            document=dict(format=body.format,id=identifier,created=datetime.now(timezone.utc).isoformat(),manifest=manifest,
                status="QUARANTINED" if invalid_count else "READY",rows=len(valid),raw_hash=raw_hash,parquet_hash=parquet_hash,
                start=valid[0]["timestamp"] if valid else None,end=valid[-1]["timestamp"] if valid else None,
                quality=dict(invalid_rows=invalid_count,errors=errors,warnings=warnings,coverage=coverage,
                    quoted_rows=sum(r["bid"] is not None and r["ask"] is not None for r in valid),zero_volume_rows=sum(r["volume"]==0 for r in valid)))
            (directory/"manifest.json").write_text(canonical(document))
            with self.db: self.db.execute("INSERT INTO datasets VALUES(?,?,?)",(identifier,document["created"],canonical(document)))
            return document

    def rows(self,identifier,instrument_id=None,limit=None,offset=0):
        self.get(identifier)
        path=self.root/"datasets"/identifier/"observations.parquet"
        table=pq.read_table(path,filters=[('instrument_id','=',instrument_id)] if instrument_id else None,use_threads=False)
        if limit is not None: table=table.slice(offset,limit)
        elif offset: table=table.slice(offset)
        return table.to_pylist()

    def verify(self,identifier):
        item=self.get(identifier)
        directory=self.root/"datasets"/identifier
        source=directory/("raw.csv" if item.get("format","CSV")=="CSV" else "raw.parquet")
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest()!=item["raw_hash"]: raise ValueError("Raw dataset integrity mismatch")
        actual=hashlib.sha256((directory/"observations.parquet").read_bytes()).hexdigest()
        if actual!=item["parquet_hash"]: raise ValueError("Dataset integrity mismatch")
        return item
