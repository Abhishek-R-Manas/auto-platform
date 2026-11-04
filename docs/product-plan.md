# Product charter and release baseline

## Purpose and product standard

Artha gives one local user a usable paper account for observing simple strategies,
inspecting decisions and reviewing results after estimated costs. Trustworthy automation
requires durable evidence of inputs, versioned rules, sizing, risk outcomes and executions.
Platform correctness and any future trading advantage have separate acceptance criteria.
Synthetic profits alone never qualify a strategy for live operation.

## Roadmap

| Phase | Deliverable | Real-money trading |
|---|---|---|
| 1 | Paper platform, optional market-data connection, basic strategy, independent risk checks, decision journal | Disabled |
| 2 | Historical backtesting, data-quality assessment, improved simulation | Disabled |
| 3 | AI candidate creation, evaluation, versioned strategy registry | Disabled |
| 4 | Forward paper evaluation, expanded stress testing, recovery drills, AI monitoring | Disabled |
| 5 | Small supervised pilot for qualifying strategies | Explicitly enabled within limits |
| 6 | Gradual automation and capital scaling | Expanded with supporting evidence |

The [complete Phase 2 plan](phase-2-plan.md) expands the Phase 2 roadmap into broad
strategy capabilities, historical-data contracts, replay and portfolio architecture,
instrument/execution models, research integrity, AI supervision boundaries and milestones
2A–2F. The owner specified a flexible development budget and broad strategy support.
The [Phase 2 implementation guide](phase-2.md) records delivered artifacts, capability
limits and owner validation. The milestone table links to that evidence.
The [Phase 3 implementation guide](phase-3.md) records AI candidate authoring, versioned
registry, evaluation, holdout and review behavior, with explicit external validation limits.

Candidate lifecycle: idea → specification → reproducible implementation → historical
assessment → forward paper evaluation → review → limited live operation → monitoring →
retirement. Keep failed candidates and evaluations. Phase 1 supplies one transparent
baseline, implementation/configuration identities and archived paper experiments.
Phase 3 adds the candidate registry and an explicitly configured research AI service.
The [Phase 4 operator guide](phase-4.md) records delivered forward paper execution, independent supervision, bounded advisory AI, recovery and qualification gates. Genuine provider validation and actual prospective strategy evidence remain separate.

## Phase 1 release criteria

| Requirement | Concrete acceptance evidence |
|---|---|
| Adjustable virtual capital and dynamic sizing | Allocation requires flat account; P&L preserved; largest eligible whole-lot size checks costs, margin and portfolio limits |
| Stocks, futures, options | Long delivery equities and options; long/short futures; lot/tick enforcement; estimated margin, daily MTM, intraday option and expiry exits |
| Manual and algorithmic orders | Both enter the same risk/OMS boundary; pure trend-crossover specification and implementation/configuration identities |
| Execution simulation | Pending limits, cancellation, whole-lot partial fills, depth consumption, adverse slippage, tick rounding and cumulative charges |
| Risk controls | Trade/open risk, independent daily/weekly/drawdown/margin/funding halts, freshness, pause and reduce-only exits |
| Options monitoring | Per-leg expiry, time remaining, Greeks, theta, quoted liquidity, and combined held-leg exposure by underlying |
| Dashboard and journal | Funds, positions, realised net and unrealised results, drawdown, durable decision reasons and linked fills |
| Decision traceability | Quotes and portfolio state known at decision time, strategy version/configuration, sizing explanation, policy and individual risk checks |
| Operations | Cash reconciliation, atomic records, readiness pause on restart, consistent backup and isolated recovery acceptance checks |
| Security | Local loopback boundary, same-origin mutations, strict schemas, server-only credentials, paper adapter enforcement and fixed broker market-data allowlist |
| Reproducible installation | Runtime and development dependency version locks for the validated Python 3.13 Linux environment |

`tests/test_trading.py`, `tests/test_api.py`, `tests/test_phase1.py` and
`tools/browser_smoke.py` provide executable acceptance evidence. Browser checks use a
separate account and produce screenshots plus `docs/browser-validation.json`. Test outcomes
and validation limits belong in `docs/phase-1.md`; simulated functionality is distinct from
verification against an authenticated broker account.

## Commercial direction

The owner's [Centaur Strategy Forge commercial map](commercial-strategy.md) positions an
AI options architect with a human guardian while retaining stocks and futures. Its three
business stages do not replace the six engineering phases or authorize a public/live launch.
Regulated activity, multi-user security and data-distribution rights require their own gates.

## Risk policy

Defaults: planned trade loss 0.25% of equity; aggregate planned risk 0.75%; daily loss 1%;
weekly loss 2%; drawdown 5%; futures margin utilisation 40%; slippage 2 basis points;
illustrative joint stress budget 5%. These are editable research settings. Changes require
flat positions and no pending orders. Capital changes preserve P&L and halts.
All entries need protective stops. Exit orders reduce existing positions. Options are
intraday, with entries blocked from 15:15 IST and exit requests from 15:20. Derivative
expiry exit requests begin at 14:45. Stocks and futures may carry overnight; futures MTM
uses simulated liquidation marks. Gap losses, stale data and thin depth can prevent a
bounded loss or timely exit. Account insolvency remains recorded.

## Data and experiment governance

Synthetic and Kite data retain their source, exchange and receipt timestamps. Validate
finite positive prices, non-crossed quotes, integral executable depth and timezone-aware
timestamps before consuming an observation. Quotes older than the configured freshness
window block entries; Kite entries also require the weekday session. Holiday calendars,
source quality scoring and historical replay are later work.

Keep the observed instrument metadata at account/source boundaries and in order evidence.
Each decision captures available quotes, portfolio positions, account values, strategy
implementation/configuration identity, risk policy identity, evaluation inputs, sizing and
execution assumptions. Cash/basis internally use integer paise; display/account amounts
and decision check amounts use rupees. Position basis in the complete raw evidence remains
integer paise. Records are inserted with state/fills in the same transaction. Application
APIs do not modify historical evidence. This is a local audit log, not a tamper-evident store.
Never fabricate evidence for trades predating the decision schema.

Archived sessions preserve unsuccessful experiments and remain queryable. Data is private,
local and retained until explicitly managed by the owner; monitor database growth and
backup before intentional removal. No automated deletion or cloud publication is enabled.

## Operations and quality measurements

Use `/api/health` and the browser's feed/risk/reconciliation status to inspect readiness.
Use `tools/backup.py` for consistent SQLite backups. Verify recovery with a separate database;
resume only after checking records and risk conditions. The server is a single process,
local workspace; failover and multi-account operations are later work.

Measure accounting correctness by reconciled cash and P&L identities; reliability by
restart, feed failure, idempotent retry and partial-fill tests; research integrity by
replaying identical strategy inputs with pinned implementation/configuration identities;
execution quality by fill reference/actual simulated price, estimated fees, paper delay,
rejections and pending reasons; usability by browser workflows and narrow-screen checks.
Trade evaluation must use results after costs, drawdown and stated simulation uncertainty.
A mature release additionally needs competitive benchmarking, specialist review, broker
validation and operational experience. Phase 1 makes no trading-edge claim.
