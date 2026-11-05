"""Zerodha market data and authentication only. There is no live order endpoint."""
import csv
import hashlib
import io
import json
import math
import os
import re
import ssl
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

import httpx

from .engine import IST


class KiteData:
    def __init__(self):
        self.key = os.getenv("KITE_API_KEY", "")
        self.secret = os.getenv("KITE_API_SECRET", "")
        self.token = os.getenv("KITE_ACCESS_TOKEN", "")
        self.last_quote_request = 0
        self.last_history_request = 0
        self.lock = threading.RLock()
        self.master_lock = threading.Lock()
        self.master_rows = []
        self.master_date = None
        self.master_hash = None

    def status(self):
        return dict(configured=bool(self.key), login_available=bool(self.key and self.secret),
                    authenticated=bool(self.key and self.token), order_access=False)

    def login_url(self, state):
        if not self.key or not self.secret:
            raise ValueError("Configure KITE_API_KEY and KITE_API_SECRET on the server first")
        return "https://kite.zerodha.com/connect/login?" + urlencode(dict(v=3, api_key=self.key, redirect_params=urlencode(dict(state=state))))

    def _request(self, method, path, *, params=None, data=None, text=False):
        allowed = {("GET", "/instruments"), ("GET", "/quote"), ("POST", "/session/token")}
        historical=method=="GET" and re.fullmatch(r"/instruments/historical/[1-9][0-9]*/(?:minute|3minute|5minute|10minute|15minute|30minute|60minute|day)",path) is not None
        if (method, path) not in allowed and not historical:
            raise ValueError("Endpoint is outside the market-data allowlist")
        headers = {"X-Kite-Version": "3"}
        if path not in {"/session/token","/instruments"}:
            if not self.key or not self.token:
                raise ValueError("Zerodha market-data session is not authenticated")
            headers["Authorization"] = f"token {self.key}:{self.token}"
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        with self.lock:
            if historical:
                delay=.4-(time.monotonic()-self.last_history_request)
                if delay>0:time.sleep(delay)
                self.last_history_request=time.monotonic()
            if path == "/quote":
                delay = 1.1 - (time.monotonic() - self.last_quote_request)
                if delay > 0:
                    time.sleep(delay)
                self.last_quote_request = time.monotonic()
            with httpx.Client(base_url="https://api.kite.trade", timeout=12, verify=context,
                              trust_env=False, follow_redirects=False) as client:
                with client.stream(method, path, params=params, data=data, headers=headers) as response:
                    if response.status_code in {401,403}:
                        self.token = ""
                        raise ValueError("Zerodha session expired or permission denied; authenticate again")
                    if response.status_code != 200:
                        raise ValueError(f"Zerodha market-data request failed (HTTP {response.status_code})")
                    content=bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content)>32*1024*1024:
                            raise ValueError("Market-data response exceeded the size limit")
        if text:
            return content.decode("utf-8")
        import json
        payload=json.loads(content)
        if payload.get("status")!="success" or not isinstance(payload.get("data"),dict):
            raise ValueError("Unexpected Zerodha response format")
        return payload["data"]

    def authenticate(self, request_token):
        with self.lock:
            if not self.key or not self.secret:
                raise ValueError("Server-side Kite credentials are not configured")
            checksum=hashlib.sha256((self.key+request_token+self.secret).encode()).hexdigest()
            data=self._request("POST","/session/token",data=dict(api_key=self.key,request_token=request_token,checksum=checksum))
            token=data.get("access_token")
            if not isinstance(token,str) or not token:
                raise ValueError("Authentication did not return a market-data session")
            self.token=token

    def _quotes(self, keys):
        return self._request("GET","/quote",params=[("i",key) for key in keys])

    def master(self):
        """Current broker master, cached once per IST date; tokens are not identities."""
        today=datetime.now(IST).date()
        with self.master_lock:
            if self.master_date!=today:
                raw=self._request("GET","/instruments",text=True)
                rows=list(csv.DictReader(io.StringIO(raw)))
                required={"exchange","tradingsymbol","instrument_token","instrument_type","lot_size","tick_size","expiry","strike","name"}
                if not rows or not required.issubset(rows[0]):
                    raise ValueError("Unexpected Kite instrument master")
                self.master_rows=rows
                self.master_hash=hashlib.sha256(raw.encode()).hexdigest()
                self.master_date=today
            return self.master_rows

    def search(self, query="", limit=40):
        query=query.upper().strip()
        rows=[r for r in self.master() if r["exchange"] in {"NSE","NFO"}
              and r["instrument_type"] in {"EQ","FUT","CE","PE"}
              and (not query or query in (r["tradingsymbol"]+" "+r["name"]).upper())]
        rows.sort(key=lambda r:(r["exchange"]!="NSE",r["tradingsymbol"]))
        return [dict(key=r["exchange"]+":"+r["tradingsymbol"],symbol=r["tradingsymbol"],name=r["name"],
                     kind=r["instrument_type"],expiry=r["expiry"],lot_size=int(r["lot_size"])) for r in rows[:limit]]

    def equity_universe(self):
        # Includes all current NSE EQ-type entries (shares, ETFs and other EQ-type securities).
        return {int(r["instrument_token"]):dict(key="NSE:"+r["tradingsymbol"],symbol=r["tradingsymbol"],
                name=r["name"],kind="EQ",exchange="NSE",instrument_token=int(r["instrument_token"]))
                for r in self.master() if r["exchange"]=="NSE" and r["instrument_type"]=="EQ"}

    @staticmethod
    def underlying_key(row):
        names={"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK","FINNIFTY":"NIFTY FIN SERVICE",
               "MIDCPNIFTY":"NIFTY MID SELECT","NIFTYNXT50":"NIFTY NEXT 50"}
        return "NSE:"+names.get(row["name"],row["name"])

    def load(self, keys=None):
        rows=self.master()
        now=datetime.now(IST)
        if keys:
            by_key={r["exchange"]+":"+r["tradingsymbol"]:r for r in rows}
            if any(k not in by_key for k in keys): raise ValueError("A selected instrument is absent from the current master")
            selected=[by_key[k] for k in keys]
        else:
            spot=float(self._quotes(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"])
            selected=[r for r in rows if r["exchange"]=="NSE" and r["tradingsymbol"] in {"RELIANCE","HDFCBANK","INFY"} and r["instrument_type"]=="EQ"]
            derivatives=[r for r in rows if r["exchange"]=="NFO" and r["name"]=="NIFTY" and r["expiry"] and
                         datetime.fromisoformat(r["expiry"]).replace(hour=14,minute=45,tzinfo=IST)>now]
            for kind in ("FUT","CE","PE"):
                candidates=[r for r in derivatives if r["instrument_type"]==kind]
                if candidates: selected.append(min(candidates,key=lambda r:(r["expiry"],abs(float(r["strike"] or 0)-spot))))
        if not selected or len(selected)>50: raise ValueError("Choose between one and fifty paper-watchlist instruments")
        if any(r["exchange"] not in {"NSE","NFO"} or r["instrument_type"] not in {"EQ","FUT","CE","PE"} for r in selected):
            raise ValueError("Only NSE equities and NFO futures/options are supported")
        if any(r.get("segment")=="INDICES" for r in selected):raise ValueError("Cash indices are chart references; choose a tradable stock, future or option for paper execution")
        refs={self.underlying_key(r) for r in selected if r["instrument_type"]!="EQ"}
        reference=self._quotes(sorted(refs)) if refs else {}
        instruments={}
        for row in selected:
            kind="EQ" if row["instrument_type"]=="EQ" else "FUT" if row["instrument_type"]=="FUT" else "OPT"
            expiry=datetime.fromisoformat(row["expiry"]).replace(hour=15,minute=30,tzinfo=IST).isoformat() if row["expiry"] else None
            if expiry and datetime.fromisoformat(expiry)<=now: raise ValueError("Expired contracts cannot be connected")
            item=dict(symbol=row["tradingsymbol"],name=row["name"] or row["tradingsymbol"],kind=kind,
                underlying=row["tradingsymbol"] if kind=="EQ" else row["name"],exchange=row["exchange"],base=0,
                lot_size=int(row["lot_size"]),tick_size=float(row["tick_size"]),expiry=expiry,
                instrument_token=int(row["instrument_token"]),master_hash=self.master_hash,master_observed_date=str(self.master_date),
                margin_rate=.18,specification="EXCHANGE_MASTER · MARGIN_ESTIMATED")
            if kind!="EQ":
                key=self.underlying_key(row); q=reference.get(key,{})
                if not q.get("instrument_token"): raise ValueError("Underlying reference quote is unavailable")
                item.update(underlying_key=key,underlying_token=int(q["instrument_token"]),
                            settlement="CASH" if row["name"] in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","NIFTYNXT50"} else "PHYSICAL")
            if kind=="OPT": item.update(strike=float(row["strike"]),option_type=row["instrument_type"])
            if item["symbol"] in instruments: raise ValueError("Ambiguous trading symbols across exchanges")
            instruments[item["symbol"]]=item
        return instruments,self.poll(instruments)

    def poll(self,instruments):
        keys=[item["exchange"]+":"+item["symbol"] for item in instruments.values()]
        refs={item.get("underlying_key","NSE:NIFTY 50") for item in instruments.values() if item["kind"]!="EQ"}
        data=self._quotes(sorted(set(keys)|refs))
        quotes={}
        for symbol,item in instruments.items():
            q=data.get(item["exchange"]+":"+symbol,{})
            buys=[v for v in q.get("depth",{}).get("buy",[]) if v.get("price",0)>0 and v.get("quantity",0)>0]
            sells=[v for v in q.get("depth",{}).get("sell",[]) if v.get("price",0)>0 and v.get("quantity",0)>0]
            if not buys or not sells or not q.get("timestamp"): continue
            stamp=datetime.fromisoformat(q["timestamp"])
            if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=IST)
            spot_quote=q if item["kind"]=="EQ" else data.get(item.get("underlying_key","NSE:NIFTY 50"),{})
            spot=float(spot_quote.get("last_price",0))
            if not math.isfinite(spot) or spot<=0: continue
            quotes[symbol]=dict(last=float(q["last_price"]),bid=float(buys[0]["price"]),ask=float(sells[0]["price"]),
                bid_quantity=int(buys[0]["quantity"]),ask_quantity=int(sells[0]["quantity"]),received_at=time.time(),
                exchange_time=stamp.isoformat(),spot=spot,iv=None,transport="REST_SNAPSHOT")
        if len(quotes)!=len(instruments):
            raise ValueError("Complete executable quotes are unavailable; check the market session and data permissions")
        return quotes
