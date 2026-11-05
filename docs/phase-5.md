# Phase 5 preparation: supervised broker pilot and market charts

Release 0.7.0, implemented 2026-11-05. **Phase 5 qualification and real-money activation are pending.** No real broker order was placed during implementation or controlled validation.

## Market data is independent of paper execution

NSE market data now includes daily, 5-minute, 15-minute and hourly close-price charts and the latest completed candle's OHLC/volume and timestamp. Select a current NSE equity or click Chart / close in the scanner. Sign in normally through Connection setup and select Refresh from Kite. Daily retrieval covers up to one year; the UI requests ten days of intraday history (the API caps intraday ranges at 60 days). Provider coverage still depends on the actual subscription.

`data/charts/charts.sqlite3` retains the latest fetched series per symbol/interval. Load saved chart works without a current login and after restart. Every series retains its source, token, master hash, fetch time and completed-candle time. Refresh failure preserves the last saved chart. Incomplete candles are omitted. A saved historical close never becomes a live quote or execution input. The old synthetic datasets cannot supply genuine prices. Until the first authenticated fetch, the chart honestly has no saved data.

Kite's access token expires at 06:00 the next day and can be revoked earlier. The app also intentionally discards browser-login tokens on restart. An instrument master contains names/contracts, not a historical price database. Those facts explain the previous empty market scanner when WAITING_AUTH; paper simulation itself does not prevent genuine data display.

## Pilot scope and gates

Open `/#pilot`. This first transport supports **manually confirmed NSE delivery-equity CNC DAY LIMIT orders only**, using an isolated broker service and SQLite journal at `data/pilot/pilot.sqlite3`. It does not dispatch strategy/AI entries, trade derivatives, sell external holdings, automatically reinvest proceeds or implement Phase 6 automation. Verified buy fills receive preauthorized native broker stop-limit exits; these risk-reducing requests are distinct from automated strategy entries.

Activation requires all of the following:

1. A qualified Phase 4 portfolio, genuine elapsed prospective evidence, current guardian approval and exact bound equity symbols. Synthetic/reference experiments cannot qualify. The current installation had zero forward portfolios at implementation time.
2. An exact broker account, reviewer, explicit total capital, daily loss threshold, planned trade-loss limit, per-order limit, daily entry count, symbols and expiry (maximum seven days). No user allocation is assumed. The initial 0.7 pilot imposed software ceilings of INR 100,000 total, INR 25,000/order, INR 10,000 daily loss and ten entries/day. These are historical: workspace 0.14 removes them and adds explicit broker-funded manual authority with no app-defined financial/count caps; see the current manual trading guide.
3. Owner confirmation of the broker's static-IP setup and delivery-sale authorization, plus explicit consent to native protective stops and risk/pause cancellation of entry remainders. These remain owner assertions and require actual broker acceptance.
4. An independently reviewed deployment enabling `ARTHA_LIVE_TRANSPORT_ENABLED=1` in the service environment. The shipped deployment does not set it. The UI cannot enable this capability. This flag is not accepted in the browser credential file.
5. A fresh read-only broker reconciliation for the same login/account, followed by typing `ARM REAL MONEY ACCOUNT_ID`. Restart pauses the pilot and clears session reconciliation.
6. A fresh risk preview (20-second lifetime), then typing `I AUTHORIZE THIS REAL MONEY ORDER` for that exact immutable intention. Each request has a unique durable identity and broker tag.

The mandate binds the exact forward evidence hash. Changed qualification, approval or evidence pauses new exposure. A forward source update may therefore require a new reviewed mandate before any order has been submitted. Automatic mandate replacement after submitted orders is intentionally unavailable; it needs a separately reviewed migration with reconciled holdings.

## Order and risk contract

The broker client has fixed verified-TLS routes for profile, equity funds, daily orders/trades, holdings/positions, regular placement and pilot-owned cancellation. It does not widen the market-data connector's allowlist. Credentials stay in server memory/private configuration and response errors are redacted.

The service commits a SENDING intention and its capital reservation before HTTP. An acknowledgement is not a fill. Timeouts and uncertain failures remain UNCERTAIN, pause entries and are never retried automatically. Reconciliation finds the existing broker order by exact ID/tag and checks authorized fields, quantities, average prices and actual trades. Duplicate tags, changed orders, unknown outcomes, missing daily-book records and trade/fill mismatches block progress. Cancellation acknowledgement remains CANCEL_PENDING until broker evidence confirms the outcome.

Entry checks reuse the shared numerical risk/funding checks and require a tick-aligned stop below the entry/current bid, round-trip estimated charges, the tighter owner/forward per-trade planned-loss budget and aggregate open-risk budget. Entry checks also include account binding/permissions, qualified evidence, reviewed calendar/session, current master lot/tick identity, fresh token-matched depth, price/spread bounds, actual conservative cash-only broker funds (excluding collateral and adhoc margin), pending reservations, lifetime gross purchase capital, per-order limits and daily entry counts. Sell requests can reduce only reconciled, unreserved units bought by this pilot. Unrelated holdings cannot be sold. Entry pause, a daily loss halt, expired authority or revoked qualification does not block a separately confirmed reduction of verified stock; execution still needs a valid broker session, fresh quotes and reviewed session/calendar. Uncertain orders must first be resolved to avoid overselling.

Daily loss uses conservative gross negative broker account P&L: gains do not offset losses and overlapping broker holding/position measures can make the halt earlier. A daily breach latches for that date. **The halt pauses new entries and requests cancellation of known entry remainders; cancellation can race or fail. It does not guarantee a maximum loss or immediate liquidation.** Verified buy fills receive exact-quantity native stop-limit requests with durable prior reservations; a stop already crossed requests a protective sell limit instead. The protection limit is two percent below its trigger/current reference with tick rounding. Gaps can leave stop-limit exits unfilled. Failed/uncertain protection halts entries, stays visible and never retries blindly. Every new fill increment gets its own protection. The owner must supervise coverage and exits in Kite, especially during the fill-to-protection interval or a session failure. Cancelling a protective order explicitly suspends protection for that symbol and pauses entries; sell only after confirmed cancellation, or use Restore reviewed broker protection. No GTT or overnight automatic renewal is installed: native stops are DAY orders, so expired/cancelled protection requires a healthy session and reviewed protection restoration. Missing stop coverage blocks further entries. Brokerage/charges are reserved estimates; final fees, contract notes and settlement need broker/owner review. Automatic strategy entries, GTT/overnight protection renewal, derivatives and final settlement reconciliation remain additional implementation before a broader Phase 5 release.

## Recovery and validation

Events are SHA-256 chained and journal rows/snapshots/mandates are verified against their event copies. A pre-restart integrity failure remains latched. The local chain has no external trust anchor. Once the broker pilot is armed, its supervision heartbeat participates in the managed-service watchdog; a stalled loop recovers through a paused restart, with uncertain intentions retained. All-store online backup includes both new databases; restore-check verifies pilot event/order/mandate/snapshot integrity. Existing source and dependency locks were preserved and verified before changes. Original paper/research/forward accounting remains isolated.

Controlled validation lives in `tests/test_pilot_charts.py` and `tools/pilot_charts_browser.py`. Fixtures exercise the real transport contract without sending broker orders; screenshots and JSON identify controlled data. Real subscribed history/quotes, static-IP order acceptance, delivery authorization, a funded pilot, actual fills and sufficient qualified prospective sessions cannot be established by these tests. Deployment/validation artifacts report actual outcomes separately.

Primary contracts checked on 2026-11-05:

- [Kite authentication and expiry](https://kite.trade/docs/connect/v3/user/)
- [Historical candles](https://kite.trade/docs/connect/v3/historical/)
- [Order acknowledgement, status, tags and trades](https://kite.trade/docs/connect/v3/orders/)
- [Holdings and positions](https://kite.trade/docs/connect/v3/portfolio/)
- [Zerodha API static-IP and rate requirements](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-is-algo-trading)


## Measured deployment

Version 0.7.0 is deployed at http://127.0.0.1:8787. Final checks passed **296 automated tests**, **47 controlled browser checks** across charts/pilot, paper desk, market data and onboarding, **nine deployed read-only desktop/mobile checks**, all nine JavaScript syntax checks and dependency consistency. No browser JavaScript errors were observed. The existing TestClient/httpx deprecation warning remains.

The pre-release source and the new execution build are preserved in verified release archives. Pre-deployment backup covered 17 databases; post-deployment backup covered 19 including charts and pilot. Both isolated restoration checks passed. The original paper financial state and candidate registry audit head were preserved.

At deployment, Kite authentication was absent, application AI was unconfigured, no forward portfolio was present, no real-money mandate was configured, broker transport was disabled and zero broker orders existed. No actual quote/history fetch, live order, native-stop acceptance or brokerage/settlement outcome is claimed from the controlled tests. Real chart verification requires the owner's fresh Kite login and an explicit Refresh from Kite. Real pilot activation additionally needs qualified prospective evidence, explicit owner limits and genuine broker acceptance. No Phase 5 completion or live strategy qualification is claimed.

- [Exact final validation and execution build](phase-5-validation.json)
- [Controlled chart/pilot browser evidence](pilot-charts-browser-validation.json)
- [Backed-up deployment and account preservation](phase-5-deployment.json)
- [Read-only deployed browser evidence](phase-5-deployed-browser.json)
- [Deployed market chart workspace](screenshots/charts-deployed-desktop.png)
- [Deployed gated broker pilot](screenshots/pilot-deployed-desktop.png)


## Manual authority in workspace 0.11

The owner requested manual real trading separately from AI/strategy qualification. The [manual trading workflow](manual-trading.md) now supports explicitly bounded manual NSE/CNC LIMIT orders through the same durable execution and protection machinery. Manual authority does not qualify a forward strategy or enable automated entries, derivatives, external-holding sales or broader Phase 5 automation. Current connection deployment and account activation are recorded separately in the manual activation artifact. The earlier qualification requirements continue to apply to the original strategy-linked mandate route.
