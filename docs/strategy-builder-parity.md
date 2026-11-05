# Strategy builder comparison · 0.15.0

Assessment date: 2026-11-05. This is a comparison of documented builder capabilities, not a claim of complete Sensibull product or proprietary interface parity. Actual acceptance outcomes are in [the studio browser record](strategy-studio-browser-validation.json) and [release validation](workspace-validation.json).

## Capability inventory

| Builder workflow | Artha behavior |
| --- | --- |
| Ready-made strategies | 51 recipes across bullish, bearish, neutral, volatility, calendar and hedged categories; explicit hypothetical or observed inputs |
| Custom strategy | Up to 20 option, equity and futures legs, quantities, expiry, strike, premium and individual IV; duplicate, reverse and exclude legs |
| Fast strike adjustments | Shift the basket, widen/narrow call-put distance and move owner-designated hedges; reject crossings, absent contracts and incompatible lot sizes |
| Expiry and quantity changes | Roll the first expiry while preserving the relative expiry intervals; multiply enabled quantities; immutable before/after inputs and undo |
| Payoff chart | Contractual single-expiry P&L, model target-date curve, breakevens, underlying marker, OI overlay and mouse/keyboard inspection |
| Profit, loss and probability | Exact contract bounds including unbounded tails; model POP and risk-neutral expected P&L retain explicit assumptions |
| Date, price and volatility scenarios | Sliders and precise target price/date inputs, individual IV assumptions and price/time P&L matrix |
| Payoff table | Configurable centre and percentage interval; target and expiry values, percentage moves, CSV download |
| Per-leg valuation | Intrinsic, time value, theoretical premium, current model P&L, target P&L and four position-unit Greeks; net Greeks at current and target assumptions |
| Prior realised P&L | Editable per-strategy manual amount and inclusion toggle; affects payoff and probability consistently; does not invent brokerage fills or alter broker balances |
| Broker position analysis | Explicit subset selection from one underlying in an audited cached portfolio; exact direction, whole lots and broker average prices; stale selection hash rejected |
| Named analysis groups | Save selected positions as a named draft and keep immutable adjustment revisions; does not rewrite broker position grouping |
| Broker basket margin | Explicit read-only Kite calculation with initial margin, final spread margin, product and existing-position scope; per-order entry charge totals; no order dispatch |
| Return on margin | Maximum contractual profit divided by the standalone basket's final broker margin; not inferred for mixed expiries, unlimited profit, nonpositive margin or existing-position scope |
| Draft tracking | Actual captured LTP marks versus unchanged entry prices, net value/P&L, expiry potential from the mark and per-leg observation times |
| Combined premium and OI charts | Observed signed quantity-weighted value/P&L for the selected basket, including straddles and strangles; distinct-contract OI; manual refresh or explicit 15-second refresh while visible |
| Advanced option-chain details | Side quotes, volume/OI, locally measured OI change, derived IV and full Greeks, intrinsic/time value; strike filter and quantity when adding legs |
| Multi-strike OI, premium and IV history | Up to six selected strikes from actual local chain snapshots, timestamps, missing-value gaps and separate metric scales |
| IV rank and percentile | Selected-strike averages over the recorded local snapshot window, with sample count; never labeled annual statistics |
| Save, reopen, compare and export | Immutable revisions, JSON import/export, saved scenario restoration, comparison with disclosed input bases |
| Strategy discovery | Scenario ranking of the 51 recipes with market-view and defined-loss filters; not advertised as Sensibull's proprietary optimizer |
| Research / forward paper | Explicit historical mapping or current-contract review; excluded legs remain excluded. Manual realised P&L is an analysis offset, not virtual starting cash |
| Mobile | Editable leg cards and scrolling data tables, with desktop/mobile controlled acceptance |

## Remaining differences

The following are not implemented by this builder release:

- Real-money futures/options basket dispatch, modifications, grouped exits and conditional basket exits. The existing live authority and order transport support manually confirmed NSE/CNC equity orders only. Implementing an analysis/margin view does not expand that authority. Derivative dispatch needs its own reviewed contract, funds/margin reservations, leg-level partial/uncertain outcomes and protective exit/recovery design.
- Genuine BSE/BFO, MCX and currency contract/quote integration. Current live identities are NSE/NFO. In particular, commodity contract multipliers and options on futures cannot be treated as generic equity options.
- Sensibull's proprietary strategy optimizer, licensed TradingView terminal/drawing suite, social verified-P&L network and its full historical datasets. The local chart library, analysis engine and recorded data are independent implementations.
- A broker trade-history import/closed-trade ledger with verified historic realised P&L. Position averages and manual offsets are explicit instead; imported broker holdings are not silently turned into strategy trades.

All displayed market data requires the owner's actual broker access. Missing past OI/IV observations cannot be generated by adding a UI control. No real order is sent during acceptance testing, and controlled fixtures do not prove actual account access or current exchange acceptance.

## References

The comparison uses Sensibull's [current product scope](https://sensibull.com/), [strike adjustment controls](https://blog.sensibull.com/2025/12/17/introducing-quicker-strike-controls/), [manual P&L workflow](https://blog.sensibull.com/2023/05/23/updates-in-strategy-builder-positions-page/), [payoff table](https://blog.sensibull.com/2023/07/06/payoff-table-on-strategy-builder-analyse-widgets/) and [supported-market guidance](https://sensibull.freshdesk.com/support/solutions/articles/43000696014-can-i-trade-currency-and-commodity-options-in-sensibull-). These establish advertised behavior, not access to its internal implementation. Broker calculations follow [Kite's margin API](https://kite.trade/docs/connect/v3/margins/).

## Data and operation

The original 0.14 source was hash-verified under `data/releases/0195d08d8f1cf6fac2f6a88c7e9d6137feb3a7317f5ab6c7e70906c086553750/` before changes. Existing SQLite records, saved mandate, order audit and cached portfolios are preserved. New immutable quote observations use `strategy_marks` in the existing workspace database, with read-time and backup hash verification. Legacy drafts retain their original stored hashes and acquire the new optional defaults only when explicitly revised.

Revalidate the exact source with `tools/validate_workbench.py`, including the studio suite and the other nine browser suites. Deployment preserves the existing inactive broker connection, verifies pre/post all-store backups and runs read-only deployed checks. Restart clears the in-memory broker session; sign in again when prompted. No credentials or private owner portfolios belong in screenshots or validation artifacts.


## Layout follow-up · 0.16

The 0.15 feature inventory remains applicable. The builder now presents legs and payoff side by side with a persistent risk/premium strip, net Greeks, scenario P&L and an explicit broker margin card. Templates use a searchable menu and secondary form settings are expandable. Controlled acceptance checks the complete four-leg overview at a 1366×768 laptop viewport, including calculated broker-fixture margin, plus mobile overflow and editable cards. This is a workflow redesign, not a claim of full Sensibull feature or visual parity.
