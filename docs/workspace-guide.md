# Trading workspace · 0.19.0

The primary navigation follows a customer’s work: **Home → Trade → Orders / Portfolio**, or **Home → Strategy Builder → My Strategies**. Connections holds broker sign-in and optional private AI configuration. Historical research, forward supervision, journal, capital/risk and live permissions remain under More tools.

## First visit

Home reads actual session and market-feed status and shows the next setup action. Save Kite app credentials once in Connections, sign in through Zerodha, then choose **Open paper desk with live quotes**. Existing valid sessions are reused; a browser refresh does not require another login. The server deliberately keeps the broker access token in memory: a server restart or expired broker session requires sign-in again. Saved charts, chain observations and strategy drafts remain available after logout. Missing prices remain unavailable.

**Explore practice trading** explicitly selects synthetic data and paper execution. Its observations are labeled practice throughout the desk. Practice prices never appear as genuine broker history.

## Trade

Keep up to 10 named watchlists with 50 instruments each. Use the + button to create a list, search to add stocks/futures/options, numbered tabs or the name selector to switch, and the ⋯ menu to rename, reorder or delete. Sort by your saved order, name, price or percentage change; toggle percentage/rupee change. Existing single watchlists migrate without losing their order. Watchlists persist independently from execution accounts and may be empty. Cash indices are chart references; their cash Buy/Sell path is blocked, while tradable futures/options can be selected separately. Choose an instrument, and inspect its chart, last quote, quote age, available depth and paper position together. Refresh quotes or fetch broker history after sign-in. Saved completed daily/intraday candles remain useful outside market hours. A separate last quote can be stale even when a historical chart is complete.

The earlier desk polled browser quotes every four seconds and plotted completed historical candles only. Version 0.9 adds same-origin server-sent events with a 250 ms sampling cadence for the current broker cache. This is not a guaranteed end-to-end market latency. The desk displays broker observation age, streaming versus snapshot source, disconnected and stale states. Watched instruments and the selected chart receive WebSocket subscription priority independently from forward and paper execution bindings. Browser streaming never consumes the execution engine’s dirty quote set or supplies fills.

Charts combine saved completed history with an LTP line and display-only developing bars built from accepted Kite observations. Daily developing bars use the provider’s daily OHLC when valid. Intraday open/high/low and volume cover observed ticks since connection; no missing ticks or intervening candles are reconstructed. Across a bucket boundary, unknown intervening volume is excluded. Bars use ordinary NSE weekday 09:15–15:30 sessions, with hourly buckets anchored to 09:15; special sessions are not inferred. Stale, out-of-order, off-session and unselected observations cannot produce new live bars. Completed history is refreshed at a bounded one-minute cadence while the desk is open. Service shutdown bounds active display streams before closing data stores. The broker token stays on the server; an EventSource URL contains only the list ID, selected instrument and interval.

The locally bundled TradingView Lightweight Charts 5.2.1 canvas supports candles/line, volume, pan/zoom, crosshair, keyboard navigation and calculated SMA20/SMA50. This is the open-source chart library, not the proprietary TradingView full terminal. No runtime chart CDN is used; licenses, notice and integrity metadata are in static/vendor.

Choose the execution account before Buy/Sell. The paper ticket retains independent risk, estimated charges, sizing, stops/targets and order evidence. The live ticket shows the actual account and connection state. Trading setup offers a separate explicit manual NSE/CNC authority, or the original qualified strategy-linked mandate. See [manual real trading](manual-trading.md). Orders and Portfolio separate paper records from explicitly reconciled broker snapshots. Reading a broker portfolio cannot enable execution.

## Portfolio

The sidebar badge follows the selected portfolio: synced Kite holdings, open broker positions, or paper open positions. Paper refreshes cannot overwrite the broker count. Unavailable broker data displays a dash. Portfolio opens on the broker Holdings tab, independently from the execution-account selector. Holdings shows total investment, synced current value, unrealized P&L and day change above a compact sortable table. Search instruments, filter products, open the row breakdown or download the visible rows as CSV. Mobile uses holding cards. Positions has separate broker P&L, realised/unrealised totals and an open/all-positions filter; closed positions remain available. The paper portfolio keeps its own tab and independent account.

The snapshot refresh button is **Sync Zerodha**. Viewing the page does not fetch a broker account automatically, enable orders or mix broker holdings into paper capital. A separate hash-verified display cache keeps holdings visible after logout and restart. Execution reconciliation is still cleared on restart; display data can never authorize orders. Recovery from a verified backup is allowed only when it matches the current audited reconciliation hash, and restores no execution or arming state. Dates and “Prices as at sync” identify the data basis; this redesign does not claim tick-by-tick portfolio repricing.

Holding quantities include reported settled, T+1 and MTF units. Cost uses the applicable standard and MTF average costs; pledged quantities are not added again, and used quantities are shown separately. Holding P&L is value minus displayed cost, excluding charges, financing and unverified corporate-action corrections. Missing amounts remain unavailable. Position P&L uses broker values, while signed position value is exposure rather than account capital. Presentation uses integer paise and never alters the stored broker snapshot or execution balances.

Design references: [Kite Portfolio manual](https://kite.trade/docs/kite/portfolio/) and [Kite quantity guidance](https://kite.trade/forum/discussion/13001/t1-t2-and-realized-qty). The design remains Artha-branded. Controlled screenshots use fictional portfolios; deployment checks do not save private holdings screenshots or exports.

## Build options strategies

Choose the underlying, expiry and input source. **Kite chain / saved data** requires actual recorded contract observations; refreshing fetches current bounded-chain strikes, premiums, depth, lot sizes, OI and timestamps. Calendars fetch the required later expiry. Missing contracts are reported, never replaced with generated market prices. **Hypothetical model** works without a broker master or sign-in and exposes editable spot, first/later expiry, illustrative lot size and strike spacing. It generates explicitly modeled analysis premiums.

The 51-template catalog covers calls/puts, debit and credit verticals, long/short straddles and strangles, iron and reverse condors/butterflies, call/put butterflies and condors, broken wings, ratios and backspreads, synthetic exposures, risk reversals, calendars, reverse calendars, diagonals, double calendars, covered calls, protective puts, collars, jade lizards, seagulls, Christmas trees and boxes. Custom baskets support up to 20 option/equity/futures legs. Edit side, strike, expiry, lots, lot size, entry price and IV; mobile uses editable leg cards.

Expiry payoff uses the supplied premiums, signed integer quantities and stated fixed cost estimate. Single-expiry bounds and isolated breakevens are calculated from all strike kinks and the complete nonnegative underlying-price domain, including unlimited tails. Earlier-date P&L and remaining calendar/diagonal time value use European Black–Scholes assumptions. Multi-expiry bounds and POP are deliberately not inferred from one scenario curve.

Move underlying price, time and IV controls to inspect target-date P&L, position Greeks and a P&L table. Model POP and model expected P&L use a risk-neutral lognormal assumption; neither predicts actual returns. Fixed costs are explicit assumptions; unknown brokerage/settlement/execution charges are not silently included. Use Broker margin for an explicit read-only Kite basket calculation; it separates initial/final margin and entry charges. Without an authenticated calculation, margin remains unavailable.

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
| Live stock pilot | Manually confirmed NSE/CNC; default transport off, installed capability recorded in deployment evidence; qualification pending |
| Live multi-leg option execution | Not implemented |
| Broker basket margin | Explicit authenticated read-only calculation |
| Full Sensibull proprietary feature parity / full TradingView drawing suite | Not claimed |
| Guaranteed returns, fills or settlement | Not claimed |

This release revamps the workflow and expands analysis. It does not complete the remaining live-money qualification and automated derivatives protection work described in [Phase 5](phase-5.md).

## Evidence and operation

Run `.venv/bin/python -B tools/validate_workbench.py` for exact-source and static-asset validation. [Validation record](workspace-validation.json), [controlled customer journeys](workbench-browser-validation.json), [deployment](workspace-deployment.json) and [deployed read-only browser checks](workspace-deployed-browser.json) record actual outcomes, with controlled and genuine observations distinguished.

Workspace drafts, chain snapshots and the persistent watchlist use a separate workspace SQLite database. All-store backup includes it and verifies immutable draft/chain content hashes on restore checks. Deployment archives source/locks/assets, checks the exact tested fingerprint, backs up all stores, requires the broker account to be inactive, explicitly preserves an already enabled connection when requested, and checks paper/candidate identity after restart.

Implementation references: [Kite WebSocket protocol](https://kite.trade/docs/connect/v3/websocket/), [Kite charting](https://kite.trade/docs/kite/charting/), [Kite orders](https://kite.trade/docs/kite/orders/), [Sensibull product scope](https://sensibull.com/) and [Lightweight Charts documentation](https://tradingview.github.io/lightweight-charts/docs). Sensibull is a product reference, not a shared backend or compatibility claim.


## Guided real-trading onboarding

Trading setup now opens a four-stage onboarding page: Connect account → Trading access → Broker permissions → Review & activate. Limits have field guidance, relationship and cash checks. Static-IP and delivery authorization have official broker instructions and explicitly labeled owner confirmations. A preflight shows individual account, funding, consent and risk outcomes, with actions for outstanding checks. Passed checks collapse into a details section. Saving persists the reviewed authority; activation remains separate. Saved authority fields rehydrate after browser refresh.

Completed account checks remain visible for the same authenticated session and trading date, while order execution still requires an independently fresh 15-second snapshot. Save and Activate refresh broker evidence. The review endpoint never saves a mandate, arms a session or submits an order. A deployment restart still clears the in-memory broker token, requiring sign-in again; cached holdings and saved authority remain intact.


In onboarding step 2, **Stock permission** offers **Choose specific stocks** or **All supported NSE stocks and ETFs**. All-stock mode removes the comma-separated list and includes eligible new listings while the saved permission remains valid. The final review names the chosen universe explicitly. Saved existing permissions retain their original selected symbols until the owner reviews and saves a scope change. Every individual order still checks the current master, quotes, depth and shared account limits.


Manual onboarding now defaults to **Broker funds · no app-defined caps**. The app sets no allocation, order-value, loss-budget or daily-entry ceilings in that mode. Custom amounts/counts remain available without hard-coded upper ceilings. Stored existing custom permissions remain bounded until explicitly reviewed and saved in another mode. Actual cash, charges, pending reservations, quotes, protection and broker restrictions are checked independently.


## Strategy builder extension · 0.15

See [the complete builder capability comparison](strategy-builder-parity.md) for strike/width/hedge controls, manual P&L, leg selection and valuation, the configurable payoff table, broker-position imports, observed strategy/multi-strike charts and explicit broker margin calculations. All these actions are analysis or read-only broker calculations. They never enable derivative execution or create fills. Manual P&L is not added to research/paper capital. History starts with actual captured observations and does not imply earlier coverage.


## Single-view builder · 0.16

On desktop/laptop the leg editor and payoff share one workspace. A six-item risk/premium strip, net Greeks, scenario P&L and broker margin remain visible together for a four-leg strategy at 1366×768. The option chain and OI tools replace the left editor area while retaining payoff and risk context. Templates open from a searchable menu. The margin action remains explicit, and missing calculations remain unavailable; calculated values retain product, position scope and observation time.

Quick strike/width/hedge controls stay near the editor. Detailed contract edits, manual P&L, notes and precise scenario settings use labeled expandable controls. The payoff table occupies the chart area when selected; it does not push the result panel down. Lower analysis tables and tracking remain available, while desktop risk and payoff panels stay in view during advanced edits. On smaller screens the payoff appears first, with a sticky risk summary and compact editable leg cards. This layout does not add execution authority or change calculations.

[Controlled laptop view](screenshots/builder-single-view-margin-controlled.png) uses fictional broker data. The studio browser suite measures actual viewport bounds and checks desktop, mobile, template selection, chain context and margin interaction.


## Visible strategy library · 0.18

The builder now lists strategies in the workspace instead of hiding the catalog in a dropdown. Choose a visible **Market view** button (All, bullish, bearish, neutral, volatility, calendar or hedged). There are no strategy-selection dropdowns or structure filter. Search combines with the chosen market view, and a matching-count label and reset action explain empty results. Filtering does not change the current basket. Selected strategy cards use a light background without a left border. Selecting an option recipe opens the options editor even if stock/futures rules were previously selected.

Each thumbnail is calculated from that recipe using normalized illustrative inputs: spot 100, strike spacing 5, 25% IV, 6.5% rate, and first/later expiries at 30/60 days. Single-expiry profiles show contract P&L; calendars retain modeled value in surviving legs and are labeled **First-expiry model**. These drawings are shapes, not market quotes or return estimates. Economically equivalent strategies may share a profile. The actual builder still uses the owner's selected contracts, inputs and costs.

The library scrolls independently beside the four-leg overview at 1366×768. Smaller screens show the filters and a horizontally scrolling strategy list. [Controlled laptop preview](screenshots/strategy-library-laptop-controlled.png) uses fictional data. The browser suite verifies filter composition, search/reset, selection, preview differences, unchanged baskets while browsing, and the retained laptop/mobile layout.

## Compact navigation · 0.19

Every page starts with a 64px icon rail. Hover over the hamburger to reveal the sidebar as an overlay; leaving the navigation collapses it. More tools opens a hover flyout that remains reachable while moving into it. Selecting a page closes both disclosures. Keyboard activation, Escape, outside dismissal and touch taps remain available. Opening either menu never changes the workspace width or trading state.
