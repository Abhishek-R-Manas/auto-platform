# Trading workspace · 0.9.0

The primary navigation follows a customer’s work: **Home → Trade → Orders / Portfolio**, or **Home → Strategy Builder → My Strategies**. Connections holds broker sign-in and optional private AI configuration. Historical research, forward supervision, journal, capital/risk and live permissions remain under More tools.

## First visit

Home reads actual session and market-feed status and shows the next setup action. Save Kite app credentials once in Connections, sign in through Zerodha, then choose **Open paper desk with live quotes**. Existing valid sessions are reused; a browser refresh does not require another login. The server deliberately keeps the broker access token in memory: a server restart or expired broker session requires sign-in again. Saved charts, chain observations and strategy drafts remain available after logout. Missing prices remain unavailable.

**Explore practice trading** explicitly selects synthetic data and paper execution. Its observations are labeled practice throughout the desk. Practice prices never appear as genuine broker history.

## Trade

Keep up to 10 named watchlists with 50 instruments each. Use the + button to create a list, search to add stocks/futures/options, numbered tabs or the name selector to switch, and the ⋯ menu to rename, reorder or delete. Sort by your saved order, name, price or percentage change; toggle percentage/rupee change. Existing single watchlists migrate without losing their order. Watchlists persist independently from execution accounts and may be empty. Cash indices are chart references; their cash Buy/Sell path is blocked, while tradable futures/options can be selected separately. Choose an instrument, and inspect its chart, last quote, quote age, available depth and paper position together. Refresh quotes or fetch broker history after sign-in. Saved completed daily/intraday candles remain useful outside market hours. A separate last quote can be stale even when a historical chart is complete.

The earlier desk polled browser quotes every four seconds and plotted completed historical candles only. Version 0.9 adds same-origin server-sent events with a 250 ms sampling cadence for the current broker cache. This is not a guaranteed end-to-end market latency. The desk displays broker observation age, streaming versus snapshot source, disconnected and stale states. Watched instruments and the selected chart receive WebSocket subscription priority independently from forward and paper execution bindings. Browser streaming never consumes the execution engine’s dirty quote set or supplies fills.

Charts combine saved completed history with an LTP line and display-only developing bars built from accepted Kite observations. Daily developing bars use the provider’s daily OHLC when valid. Intraday open/high/low and volume cover observed ticks since connection; no missing ticks or intervening candles are reconstructed. Across a bucket boundary, unknown intervening volume is excluded. Bars use ordinary NSE weekday 09:15–15:30 sessions, with hourly buckets anchored to 09:15; special sessions are not inferred. Stale, out-of-order, off-session and unselected observations cannot produce new live bars. Completed history is refreshed at a bounded one-minute cadence while the desk is open. Service shutdown bounds active display streams before closing data stores. The broker token stays on the server; an EventSource URL contains only the list ID, selected instrument and interval.

The locally bundled TradingView Lightweight Charts 5.2.1 canvas supports candles/line, volume, pan/zoom, crosshair, keyboard navigation and calculated SMA20/SMA50. This is the open-source chart library, not the proprietary TradingView full terminal. No runtime chart CDN is used; licenses, notice and integrity metadata are in static/vendor.

Choose the execution account before Buy/Sell. The paper ticket retains independent risk, estimated charges, sizing, stops/targets and order evidence. The live ticket shows the separately gated, manually confirmed NSE/CNC pilot, including its actual disabled state. Orders and Portfolio separate paper records from explicitly reconciled broker snapshots. Reading a broker portfolio cannot enable execution.

## Build options strategies

Choose the underlying, expiry and input source. **Kite chain / saved data** requires actual recorded contract observations; refreshing fetches current bounded-chain strikes, premiums, depth, lot sizes, OI and timestamps. Calendars fetch the required later expiry. Missing contracts are reported, never replaced with generated market prices. **Hypothetical model** works without a broker master or sign-in and exposes editable spot, first/later expiry, illustrative lot size and strike spacing. It generates explicitly modeled analysis premiums.

The 51-template catalog covers calls/puts, debit and credit verticals, long/short straddles and strangles, iron and reverse condors/butterflies, call/put butterflies and condors, broken wings, ratios and backspreads, synthetic exposures, risk reversals, calendars, reverse calendars, diagonals, double calendars, covered calls, protective puts, collars, jade lizards, seagulls, Christmas trees and boxes. Custom baskets support up to 20 option/equity/futures legs. Edit side, strike, expiry, lots, lot size, entry price and IV; mobile uses editable leg cards.

Expiry payoff uses the supplied premiums, signed integer quantities and stated fixed cost estimate. Single-expiry bounds and isolated breakevens are calculated from all strike kinks and the complete nonnegative underlying-price domain, including unlimited tails. Earlier-date P&L and remaining calendar/diagonal time value use European Black–Scholes assumptions. Multi-expiry bounds and POP are deliberately not inferred from one scenario curve.

Move underlying price, time and IV controls to inspect target-date P&L, position Greeks and a P&L table. Model POP and model expected P&L use a risk-neutral lognormal assumption; neither predicts actual returns. Fixed costs are explicit assumptions; unknown brokerage/settlement/execution charges are not silently included. Actual broker SPAN margin is not computed.

**Find a strategy** screens templates by the selected price/date/IV scenario, category and maximum defined loss. Its ranking is scenario P&L. Loading a result retains the scenario basis. **Save strategy** stores an immutable revision, including its inputs and scenario. My Strategies can reopen, compare two to six drafts and export analysis JSON; comparisons disclose different input bases.

## Stock/futures rules and testing

The rule builder exposes trend, mean-reversion, breakout, momentum, volatility and basic rules parameters plus direction, lots, stops, targets, allocation and overnight choice. It creates the same bounded shared strategy specifications used by the research engine.

Backtest requires an explicit compatible historical dataset and leg/underlying mapping. A current option symbol is never silently substituted into old data. Option baskets enter at the first eligible positive reference observation; rule strategies use their saved signal. Synthetic datasets remain labeled and cannot qualify a strategy. Historical execution uses dataset prices and assumptions, not the builder entry-premium illustration.

Forward paper testing requires actual current contract bindings, matching strike/expiry/lot sizes, named reviewer, data rights, calendar and model review. It creates a **paused reference portfolio** with its own virtual capital. Warm-up and activation are separate actions. A saved draft or simulated return does not confer live eligibility. AI ideas use a separately configured account and a bounded explicit request; no model call runs automatically on visiting a page.

## Capability boundaries

| Capability | Current behavior |
| --- | --- |
| Guided home, watchlist, charts, paper orders | Implemented |
| 51 templates, custom baskets, payoff/scenarios, Greeks, model POP | Implemented with assumptions described above |
| Option chain, bounded OI/PCR/max-pain, IV smile/history | Actual saved observations; IV is derived; history begins when collected |
| Draft revisions, compare, scenario screening, export | Implemented |
| Historical and forward paper handoffs | Explicit mapping/review; independent isolated accounts |
| Real broker portfolio | Explicit reconciliation, cached holdings/positions |
| Live stock pilot | Manually confirmed NSE/CNC implementation, transport OFF; qualification pending |
| Live multi-leg option execution and broker SPAN margin | Not implemented |
| Full Sensibull proprietary feature parity / full TradingView drawing suite | Not claimed |
| Guaranteed returns, fills or settlement | Not claimed |

This release revamps the workflow and expands analysis. It does not complete the remaining live-money qualification and automated derivatives protection work described in [Phase 5](phase-5.md).

## Evidence and operation

Run `.venv/bin/python -B tools/validate_workbench.py` for exact-source and static-asset validation. [Validation record](workspace-validation.json), [controlled customer journeys](workbench-browser-validation.json), [deployment](workspace-deployment.json) and [deployed read-only browser checks](workspace-deployed-browser.json) record actual outcomes, with controlled and genuine observations distinguished.

Workspace drafts, chain snapshots and the persistent watchlist use a separate workspace SQLite database. All-store backup includes it and verifies immutable draft/chain content hashes on restore checks. Deployment archives source/locks/assets, checks the exact tested fingerprint, backs up all stores, requires real transport OFF and checks paper/candidate identity after restart.

Implementation references: [Kite WebSocket protocol](https://kite.trade/docs/connect/v3/websocket/), [Kite charting](https://kite.trade/docs/kite/charting/), [Kite orders](https://kite.trade/docs/kite/orders/), [Sensibull product scope](https://sensibull.com/) and [Lightweight Charts documentation](https://tradingview.github.io/lightweight-charts/docs). Sensibull is a product reference, not a shared backend or compatibility claim.
