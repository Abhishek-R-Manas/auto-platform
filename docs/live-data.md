# Live NSE data: implementation and operation

Date: 2026-11-04. All executions and account balances remain PAPER.

## Delivered and connection status

The application now has an **NSE market data** page at `http://127.0.0.1:8787/#marketdata`.
It can subscribe to every **current NSE EQ-type entry** in the authenticated provider's daily
instrument master, including shares, ETFs and other EQ-type securities. It does not assert
that this is a complete historical listing universe. No small hard-coded stock list is used
for the breadth scanner. Counts show master entries, received symbols, fresh symbols and
missing/stale symbols. Search and pagination cover the entire loaded universe.

The implementation is connected to Kite Connect's documented market-data APIs. The deployment
currently has **no API key, API secret or authenticated access token**. The public instrument-master endpoint was verified from this server: **115,628 total instruments
and 10,405 current NSE EQ-type entries** were returned on 2026-11-04. The master now loads
without a price session, so names and contract metadata are available before authentication.
Actual NSE prices have **not** been validated: price fields remain missing until a genuine
data session is authenticated.
Synthetic data remains labelled in the existing paper workspace; it is never inserted into
the live market scanner. See [deployment evidence](live-data-deployment.json) for measured status.

## Portfolio and data-source assessment

Broad coverage is useful for liquidity screening, cross-sectional signals and portfolio
selection. Current listings alone cannot establish historical research integrity: delisted
names, point-in-time membership, corporate actions, contract specifications and unavailable
history still need suitable datasets. A live quote does not establish historical liquidity,
borrow availability, deliverable inventory, exchange margin or an executable real-world fill.
Options Greeks and paper margin remain estimates. The breadth feed supplies observed data;
independent risk checks still control every paper order.

NSE offers direct multicast/leased-line subscriptions or authorized data vendors. Its website
terms prohibit systematic automated website collection. This release uses the existing broker
connector rather than website scraping. There is no unauthenticated NSE website endpoint,
proxy, cookie bypass or delayed-data substitute presented as a live exchange feed.

The provider documents 3,000 tokens per connection and three connections per API key. The real
master exceeds that 9,000-token allowance. The hub prioritizes the paper watchlist and its
underlying references for streaming, then fills the remaining streaming capacity with equities.
Every overflow equity is covered by a rotating REST snapshot batch of at most 500 instruments,
with requests spaced by at least 1.1 seconds. Counts and rows identify streaming versus snapshot
coverage; no symbols are silently omitted. This is genuine snapshot sampling for the overflow,
with a refresh cycle that grows with the number of batches, rather than a claim that every
symbol is streamed. Other applications sharing the API key can consume its connection allowance.
Exact subscribed entitlements and actual available symbols require real-account validation.

## Guided setup

Use **Connection setup** at `/#setup` for private app-key entry, normal Zerodha login,
independent quote/history checks and feed connection without restarting. See [onboarding](onboarding.md).
The advanced manual setup below remains available.

## Private setup and use

1. Obtain a Kite data subscription with the required NSE/NFO entitlements. This release does
   not purchase subscriptions or enable broker order access.
2. Configure the Kite application callback to
   `http://127.0.0.1:8787/api/kite/callback`, matching the SSH-tunnel browser address. Follow
   the broker's accepted redirect configuration if it requires another registered address.
3. Store `KITE_API_KEY` and `KITE_API_SECRET` in the server's existing private environment or
   `.env.local` (mode **600**, outside the static directory). `.env.example` supplies names.
   Optionally provide `KITE_ACCESS_TOKEN` instead of using browser authentication. Never paste
   secrets into chat, URLs, documentation, screenshots or strategy specifications.
4. Restart the local launcher after changing file/environment credentials. In **Settings**,
   select **Authenticate with Zerodha** for the normal broker login. Tokens expire according
   to the broker session lifecycle; the app does not bypass login or fabricate token renewal.
5. Open **NSE market data**, then **Connect all NSE equities**. Inspect coverage, connection
   phase, exchange timestamps and freshness. A socket heartbeat never refreshes an old quote.
6. Use **Add to watchlist** or Settings' current-contract search. The paper watchlist accepts
   up to 50 exact `NSE:SYMBOL` / `NFO:CONTRACT` entries, separate from the full breadth universe.
   **Use Kite quotes** applies the selection only while the paper account is flat with no
   active orders. A blank selection uses the existing stock/futures/options sample.
7. Start watchlist recording from the market page. Rights confirmation records the owner's
   assertion; it does not grant data rights. Stop the capture and choose **Create research
   dataset**. Inspect the immutable dataset and quality warnings in Research lab.

For remote access, retain the existing tunnel:

```bash
ssh -N -L 8787:127.0.0.1:8787 YOUR_SSH_USER@tsw-lnx-93-30.cisco.com
```

Then open `http://127.0.0.1:8787/#marketdata`. The service remains bound to loopback.

## Data processing, freshness and recovery

- Full binary NSE/NFO and index packets use validated lengths, counts and the documented
  paise conversion. Unknown, truncated, future-dated and out-of-order packets cannot supply
  paper liquidity. Illiquid/crossed books may supply a displayed last price without an
  executable bid/ask. Other exchange segments and text order postbacks are excluded.
- REST uses TLS 1.3. The actual WebSocket endpoint closed TLS-1.3-only handshakes but
  accepted verified TLS 1.2, so the stream permits TLS 1.2+ with forward-secret ECDHE AEAD
  ciphers and certificate/hostname verification. TLS 1.3 remains preferred when available.
  This is a documented compatibility exception to the CodeGuard TLS-1.3-only guideline;
  legacy protocols and certificate bypasses remain disabled. Only `wss://ws.kite.trade` is
  used. Redirects and environment proxies are disabled. Broker-required authentication
  query parameters remain server-side; transport debug logs and exception text are suppressed.
- Connection phases distinguish loading, connecting, streaming, retry and missing authentication.
  Freshness separately requires exchange and receipt ages of at most 30 seconds. Future
  exchange timestamps beyond two seconds and missing underlying references are rejected.
- The paper pump coalesces updates to at most one batch per second, preserving actual receipt
  times. Unchanged instruments gain neither new simulated liquidity nor invented strategy
  observations. The engine rejects an obsolete watchlist and blocks orders on feed failure.
- Any socket failure closes the group, records a capture gap and reconnects with exponential
  backoff up to 30 seconds. A rejected authenticated handshake clears the session and requires
  login. Daily master refresh rotates subscriptions. Gaps are recorded, not backfilled with
  invented ticks. This is not an exhaustive, sequence-reconciled exchange tick archive.
- On restart, public metadata refreshes independently. Persisted price-feed enablement resumes only if authentication exists. Active captures
  become INTERRUPTED; they do not silently continue across a restart. New account entries
  retain the engine's normal restart readiness pause.

## Recording and research contracts

The breadth scanner retains latest quotes in memory. Explicit recordings cover the **paper
watchlist**, not every breadth symbol. Captures are stored separately in
`data/market-data/market.sqlite3`, capped at **100,000 rows per session**, with a visible limit
status and no automatic deletion. Monitor disk use and back up this database with SQLite's
online backup API as well as backing up account/research data.

Stopped captures import through the existing raw CSV / normalized Parquet catalog. Provenance
includes the capture id, source kind BROKER, master hash, stable exchange/symbol identity,
contract metadata, recording status, gap count and owner rights assertion. Numeric broker
tokens are not permanent identities. Research timestamps denote local information availability
(receipt time); raw CSV preserves both receipt and exchange times. This prevents a recorded
signal from appearing available before the server received it.

Quotes have actual bid/ask quantities. Cumulative provider volume is retained as raw evidence;
canonical quote volume is zero to avoid treating daily cumulative volume as new turnover at
every observation. Recording does not infer historical margins, complete corporate actions or
missing sessions. Outside-session observations and other quality problems are quarantined by
the catalog. Reimport is idempotent; existing research datasets remain immutable.

## APIs and validation

New read APIs: `/api/market/status`, `/api/market/universe?q=&offset=&limit=`,
`/api/market/search?q=`, `/api/market/captures`.

Mutations: `/api/market/connect` (`enabled`), `/api/market/source` (`source`, optional
`instruments`), `/api/market/captures` (`name`, `rights_confirmed`), and capture
`/{id}/stop`, `/{id}/dataset`. Existing same-origin, JSON, size and strict-schema boundaries
apply. No user-controlled upstream URL, SQL, file path or live-order route is accepted.

Controlled automated tests cover protocol framing, all-symbol batching, malformed/dead/stale
feeds, out-of-order rejection, exception redaction, source isolation, per-symbol liquidity,
strategy history, recording provenance, restart interruption, immutable imports and missing
credentials. The dedicated Chromium workflow covers scanner pagination/search, instrument
selection, stream delivery, recording, Research lab handoff and mobile layout. Its instrument
names and screenshots explicitly identify controlled fixtures; it does not validate real NSE
prices. Existing paper and research browser workflows are also checked.

CodeGuard review checked credentials, approved cryptography and certificate verification, plus
Python/JavaScript/API input, authentication, authorization, session, storage, file, logging,
privacy and dependency rules. No embedded certificates or real secrets were introduced. See the documented broker TLS compatibility exception above. The
local workspace continues to rely on SSH/network isolation and private files; multi-user
internet authentication, managed secret storage, encrypted backups and independent security
assessment remain deployment hardening work, not capabilities established by these checks.

## Primary references checked on 2026-11-04

- [Kite WebSocket protocol and connection limits](https://kite.trade/docs/connect/v3/websocket/).
- [Kite instrument master, quote snapshots and token reuse](https://kite.trade/docs/connect/v3/market-quotes/).
- [Kite authentication/session lifecycle](https://kite.trade/docs/connect/v3/user/).
- [NSE direct feeds and authorized vendors](https://www.nseindia.com/static/market-data/real-time-data-subscription).
- [NSE website terms](https://www.nseindia.com/static/nse-terms-of-use).
- [WebSocket client security and configuration](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html).

Provider probes: [public master and TLS compatibility](live-data-provider-check.json),
[REST/TLS diagnostics](live-data-network-diagnostics.json). The earlier TLS-1.3-only WebSocket
probe failed; it is preserved in [initial probe](live-data-network-check.json), rather than
being mistaken for a successful session.


## Deployed validation

The local service was deployed on 2026-11-04 with consistent account and catalog backups.
The running service loaded **10,405 genuine NSE EQ-type instrument entries** and reported
**WAITING_AUTH**, with zero market-price observations and no authenticated broker session.
Account reconciliation passed, existing records were retained and new paper entries are
paused after restart. No real-money execution is available.

Checks: **118 automated tests**, **14 paper-desk browser workflows**, **13 Research lab
browser workflows**, and **10 controlled market-data browser workflows** passed. JavaScript
syntax, dependency consistency and local documentation links passed. A separate read-only
browser check verified the actual deployed instrument list, RELIANCE search, missing-price
labels and authentication controls, with no JavaScript errors.

- [Release checks](live-data-validation.json).
- [Controlled market-data browser evidence](market-browser-validation.json).
- [Actual deployed browser evidence](live-data-deployed-browser.json).
- [Actual deployed metadata screenshot](screenshots/market-data-deployed-real-metadata.png).
- [Compatible WebSocket endpoint probe](live-data-compatible-network-check.json): the server
  reached the real endpoint using verified TLS 1.2+, and the unauthenticated request was
  rejected. This verifies connectivity, not a subscribed quote session.

Complete private credential setup and broker authentication to receive genuine market prices.
