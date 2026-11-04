# Phase 2 implementation and validation handoff

**Date:** 2026-11-04  
**State:** Research workflow implemented; owner validation of datasets and economic assumptions is pending.  
**Mode:** Historical research and paper simulation only.  
**Planning baseline:** [Complete Phase 2 plan](phase-2-plan.md).

## Try the complete workflow

Open **Research lab** at `http://127.0.0.1:8787/#research` using the existing SSH tunnel.
Research accounts and files live under `data/research/`, separate from the main paper desk.

1. Open **Data catalog**. Import CSV/Parquet with a manifest, use the authenticated Kite
   candle import, or explicitly create the labelled synthetic test dataset.
2. Inspect source provenance, invalid rows, session gaps, raw/normalized hashes and contracts.
   Invalid imports remain **QUARANTINED** and cannot execute.
3. Choose **Inspect & use** and select a reference strategy and virtual capital.
4. Expand **Complete strategy and run specification** for legs/selectors, product types,
   conditions, sizing, limits, windows, training cutoff and execution assumptions.
   The full JSON specification is authoritative.
5. Run **Check capabilities** and inspect blockers and assumptions. Queue the experiment.
6. Follow **Run queue**, including pause/resume/cancel. Inspect the report, strategy books,
   equity/drawdown, exposure, orders and event evidence. Filter FILL/ACCEPT/REJECT/SETTLEMENT
   and click **Evidence** to inspect information available at that event.
7. Run cost sensitivity or walk-forward folds. Select completed runs for comparison.
8. Export the evidence bundle or prepare an immutable **paper review packet**. A packet
   does not activate a strategy or submit broker orders.

The [synthetic manifest](research-examples/synthetic-manifest.json) and
[synthetic prices](research-examples/synthetic-prices.csv) reproduce the bundled software
fixture. Its prices, contracts, rates and margins are generated, not historical market data.

## Milestone mapping

| Milestone | Delivered artifacts | Acceptance and owner validation |
|---|---|---|
| 2A — Capabilities/data contracts | `schemas.py`, `signals.py`, manifests and preflight | Contracts/blockers tested; supplier selection, rights and real coverage remain owner validation |
| 2B — Historical data | `catalog.py`, immutable raw/Parquet files, revisions and quality reports | Import, integrity, quarantine, duplicates and availability checks tested; completeness flags remain source assertions |
| 2C — Replay/account core | `replay.py`, `jobs.py`, shared financial/risk primitives | Isolated books, capital reservations, deterministic replay, checkpoint/resume and exclusive worker leases tested |
| 2D — Instruments/execution | Lots, intraday/delivery products, futures MTM, selectors/rolls, option legs and settlement | Independent accounting and adverse-path examples tested; exact portfolio margin, queues and settlement charges remain limited |
| 2E — Research/evaluation | `metrics.py`, windows, training-only ridge, sensitivity and walk-forward | Future-access, training-window, attribution and repeated-run evidence tested; strategy suitability requires review |
| 2F — Product/handoff | Research lab, comparison, reports, evidence, exports and review packets | Actual browser workflows tested; advanced strategies are not automatically activated in the original paper desk |

The original paper desk and research engine share money conversion, charge calculations,
numerical budget checks, cash-flow equations, top-of-book execution proposals and trend
rules. Research adds event-clock order handling and strategy books/FIFO lots. Review packets
form the boundary for later forward paper integration; the Phase 1 strategy toggle does
not claim to run every research family.

## Data contract and units

A manifest declares source/source_kind, rights, frequency, timestamp semantics, calendar,
stable instruments, corporate actions and effective fee profiles. Dataset ids bind raw
input and manifest hashes; normalized Parquet has a separate integrity hash. Reimporting
identical input is idempotent. Corrections use `parent_dataset_id` and `revision_note`.
Existing versions are not overwritten.

CSV is UTF-8; the JSON API accepts Parquet as base64. Imports are bounded to 250,000 rows,
24 MB in the browser, and a 50-year span per revision. Compressed Parquet expansion is
bounded before reading. Larger collections need explicit versioned batches. PyArrow is the
query layer; the incompatible DuckDB wheel is not a project dependency.

| Column | Meaning |
|---|---|
| `instrument_id` | Stable manifest id; broker tokens are not permanent historical identities |
| `timestamp` | Aware observation/bar timestamp; DAY date-only values use the configured session |
| `open,high,low,close` | Raw rupees per unit; consistent OHLC |
| `volume` | Units, not lots; convert provider contract volumes using dated lot sizes |
| `bid,ask,bid_quantity,ask_quantity` | Required for QUOTE data; actual prices and units |
| `oi` | Open-interest units when supplied |
| `underlying_price` | Underlying value known as of this record |
| `iv` | Annual fraction, e.g. 0.20; otherwise inferred when permitted by model bounds |
| `settlement` | Supplied futures settlement price per unit; missing values use a labelled close estimate |
| `margin_per_lot` | Supplied gross rupee requirement for one dated lot |
| `borrow_quantity,borrow_rate` | Available stock-borrow units and annual cost fraction |
| `halted` | `true`/`1` prevents execution |
| `features_json` | Numeric features with `value` and aware `available_at` |

Frequency is DAY, MINUTE or QUOTE. START candles become completion-time observations before
signals see them. MINUTE data declares interval length. Calendars include timezone, holidays,
special sessions and settlement-day counts. They are versioned inputs; their presence does
not establish independent holiday verification.

Instruments declare EQ/INDEX/FUT/OPT, underlying, expiry/strike/CE/PE, CASH/PHYSICAL
settlement, listing dates and effective lot/tick terms. Adjusted-only prices are blocked
for execution; import raw prices and dated actions. Zero expired-option settlement values
do not supply executable liquidity. Rights/completeness flags remain owner/source assertions.

Actions include `known_at` and `effective_at`. Splits/contract adjustments update lots and
only history available at that event. Dividends accrue and pay on supplied dates. Missing
fractional/delisting settlement evidence remains an exception rather than disappearing.

## Strategies and controls

Implemented families: TREND, MEAN_REVERSION, BREAKOUT, MOMENTUM, PAIRS, SCHEDULED, RULES,
LINEAR_MODEL and VOLATILITY. Specs declare identity/version, references, legs, allocation,
sizing, stops/targets, holding horizon, shorts and optional rolls. Templates have no established
edge and their parameters are not investment recommendations.

A leg uses one instrument or selector, a signed ratio and AUTO/DELIVERY/INTRADAY/DERIVATIVE
product. Quantities are `basket lots * ratio * dated instrument lot_size`. Equity lot size
is one; equal ratios do not imply equal notional hedges. Selectors use the observed chain,
expiry rank/range and ATM plus strike-index offset or closest absolute delta. Positive
strike offsets select higher strikes. Duplicate resolved contracts are rejected.
`roll_on_expiry` closes the group before selecting later contracts and overrides SETTLE for
that strategy. Long/short reversal closes old exposure before opening the opposite side.

RULES use bounded all/any/not trees and whitelisted comparisons/features. Missing values stay
unavailable through boolean negation. Requests cannot execute Python/eval code. LINEAR_MODEL
fits lag-return ridge only inside its declared training window/cutoff. AI candidate generation
remains Phase 3.

Books share a constrained capital pool. Rechecks cover whole lots, lifecycle, freshness,
allocation, planned risk, reservations, funding, margin, concentration and joint stress.
Stock shorts need intraday treatment or supplied borrow; overnight borrowing incurs supplied
costs. Daily data cannot represent an intraday strategy. Hedges submit first; protected short
options wait for sufficient filled protection. Uncovered options need explicit enablement
and every other control. Rejected legs request an unwind; failed exits remain pending.

## Execution, accounting and limitations

Lifecycle includes acknowledgements, partial fills, pending cancellation/races, liquidity,
TTL, stops/targets, gaps and rejection sensitivities. DAY entry remainders expire after their
active session; close-time intentions can activate next session. Reduce-only exits persist.

Bar fills use next eligible open plus declared spread/slippage, capped by observed bar
volume. Exact intrabar timing is unknown. Stop/target collisions emit the recorded adverse-
or favorable-first assumption. Quotes consume observed top-of-book depth. **Order-book
queues, market impact and high-frequency market making require an additional validated
implementation and are blocked capabilities now.**

Dated charge profiles apply rate changes only to new partial-fill turnover. DP is estimated
once per instrument/day. Available funds account for reservations and supplied settlement
rules. Futures MTM moves cash and resets basis without manufacturing equity. Option value,
IV, Greeks and stress use configured interest and dividend yield.

Expiry can request exits or settle. Physical delivery uses explicit exercise-value and
underlying-transfer accounting. Unknown data and delivery/funding shortfalls remain visible;
settlement charges are estimates. HISTORICAL_GROSS uses supplied contract requirements;
CONSERVATIVE_GROSS uses declared rates. Neither claims an exact SPAN/portfolio-offset engine.
Negative cash/equity is retained. Gross strategy-book margin does not invent broker netting
benefits. Benchmark curves are labelled price returns, without implied dividend reinvestment.

## Reproducibility and operations

Two process workers have isolated SQLite WAL accounts. Queue depth is capped at 64, with a
300-second attempt budget. Pause/cancel is checked between replay steps. Events, orders and
checkpoints commit together. Exclusive leases prevent duplicate execution after a retry.
Inactive interrupted jobs pause for explicit resume; active orphan workers retain their lease.

Artifacts record source/config/dataset hashes, Python/package versions, model state, seed
and economic hash. Incompatible code-build resume is rejected. Repeated runs are tested for
matching economic results. Bootstrap diagnostics use the seed; default fills are deterministic.

Training/validation/holdout roles are chronological and disjoint. Walk-forward folds have
separate accounts and cutoffs. Retain failed trials and all sensitivity variants. Search
statistics and block-bootstrap intervals carry sample/dependence limitations and do not
establish a trading edge or correct bad data/execution assumptions.

Use `/api/docs` for Historical research schemas. Main endpoints under `/api/research`:

- `datasets`, `datasets/import`, `datasets/synthetic`, `datasets/kite`, dataset `rows/manifest`.
- `templates`, `capabilities`, `preflight`.
- `runs`, run `control/report/curve/orders/events/export/handoff`.
- `suites`, `walk-forward`, `compare`, `monitor/events`.

State lives in `data/research/catalog.sqlite3`, content-addressed `datasets/` and independent
`runs/{id}/run.sqlite3` plus config/report JSON. The API accepts no arbitrary filesystem paths
or SQL. Worker environments remove broker credential variables. Historical Kite requests
have a numeric-token/interval GET allowlist; order endpoints remain unavailable.

The monitoring contract reports deterministic health with `ai_service_connected=false`.
Paper packets retain `live_orders_enabled=false`. Authentic broker permissions, licensed
historical coverage and economic-model acceptance require owner validation. Full AI supervision
remains Phase 4; no fabricated AI output is presented.

## Validate with your data

First exercise the bundled fixture, then import your licensed raw history and actual
contracts, calendar, charges and margin assumptions. Compare hand-calculated trades,
corporate actions, expiry/rolls and capital constraints with the event evidence. Review
remaining obligations and simulation limits before forward paper evaluation.

Local results: [Phase 2 validation](phase-2-validation.json), [research browser evidence](research-browser-validation.json)
and [paper browser evidence](browser-validation.json). From the project root:

```bash
.venv/bin/python -m pytest -q
node --check static/app.js
node --check static/research.js
.venv/bin/python -m pip check
.venv/bin/python tools/browser_smoke.py
.venv/bin/python tools/research_browser.py
```

All acceptance checks use disposable accounts. Pre-change source is preserved in
`.before-phase2-20261104/`. Deployment backs up the main account consistently before restart.
A restart pauses new main-account entries for readiness review. No real-money adapter is installed.


## Deployed build

The local service was restarted on 2026-11-04 after a consistent main-account backup.
The deployed build completed **Deployment validation — synthetic stock swing** in the
isolated research workspace. Its cash ledger and strategy attribution reconciled.
See [deployment evidence](phase-2-deployment.json) for the run id, loaded source hash and backup.
The main desk's new entries are paused after restart; research runs use separate accounts.

Release checks: **93 automated tests passed**, **13 Research lab browser workflows passed**,
and **14 paper-desk browser workflows passed**. Both browser runs reported no JavaScript
errors. JavaScript syntax, installed dependency consistency, documentation links and example
manifest validation passed. The test client emits one recorded dependency deprecation warning.
These checks validate implementation behavior using controlled inputs; they do not replace
owner review of authentic historical data, trading assumptions or strategy suitability.


## Live-data extension

The [live NSE integration](live-data.md) adds the current provider equity universe, streaming
quotes, a selectable paper watchlist and forward quote captures that can become immutable
research datasets. This does not supply missing historical listings, corporate actions,
expired option history or complete historical margin. Provider authentication is separately
reported; controlled transport tests are not evidence of a genuine market session.
