# Agent guidance for Artha

## Read first

- [B2B MVP roadmap and current handoff](docs/mvp-validation-roadmap.md): MVP-1 and MVP-2 engineering are implemented in **Strategy Contracts**. MVP-3 adds the separate **Team workspace** sidebar link and authenticated organization-only application. See [MVP-1](docs/mvp-1/README.md), [MVP-2](docs/mvp-2/README.md) and [MVP-3 operating/recovery guide](docs/mvp-3/README.md). Check actual validation/deployment records. Genuine off-host destination/recipient acceptance, customer/source acceptance and public team-host acceptance remain pending. MVP-4 software validation is implemented and deployed; see [MVP-4](docs/mvp-4/README.md) and its actual validation/deployment records. Independent financial reviewer and genuine-data acceptance remain pending, alongside those open MVP-3 dependencies. MVP-5 operating software is implemented and deployed; see [MVP-5](docs/mvp-5/README.md) and its actual validation/deployment record. Genuine repeated customer work, payment/renewal verification and four-week operation remain pending. These MVP stages are separate from engineering Phases 1-6 and do not expand live-trading authority.

- [Workspace 0.20 guide](docs/workspace-guide.md): guided Home, trading desk and 51-template builder. The workspace revamp does not activate or qualify Phase 5. Main document is `static/workspace-v020.html`, with `shell-v019.js`, `navigation-v020.js`, `navigation-v019.css`, `contract-steps-v020.css`, `workspace-v020.js`, `strategy-studio-v016.js`, `builder-layout-v017.js`, `builder-layout-v016.css`, `template-library-v018.css`, `strategy-studio-v015.css`, `workspace-v09.css`, `portfolio-v011.js`, `portfolio.css`, `pilot-workbench-v014.js`, `live-v011.css`, `trading-onboarding-v014.js`, `trading-onboarding-v012.css` and locally licensed chart vendor assets. Legacy files remain for archived releases. Workspace data has independent SQLite storage and hash-verified draft/chain backups.

- [Phase 5 preparation](docs/phase-5.md): cached broker charts and the isolated, disabled-by-default manually confirmed NSE/CNC pilot. Qualification, owner limits, genuine broker acceptance and broader automated protection remain pending. Do not represent this as a fully qualified or activated Phase 5 release.


This project is a local paper-trading foundation for a broader AI trading platform.
Before further research planning or implementation, read:

- [Dated research inputs](docs/inputs.md): official FII/DII report imports, MoSPI/RBI public feeds, Kite IV/skew, timestamp/revision contracts and source validation/deployment evidence.

- [Phase 4 implementation and operator guide](docs/phase-4.md), validation and deployment evidence.
- [Phase 4 plan](docs/phase-4-plan.md) for forward paper execution, supervision and release gates.
- [Commercial strategy](docs/commercial-strategy.md) for the owner's Centaur Strategy Forge direction, human guardian, options differentiation and separate commercial/engineering roadmaps.

- [Connection onboarding](docs/onboarding.md) for in-app Kite key storage, login, data checks and configuration precedence.

- [Phase 3 implementation and operator guide](docs/phase-3.md),
  [Phase 3 baseline](docs/phase-3-plan.md), and release/deployment evidence before AI work.

- [Live NSE data integration](docs/live-data.md): all-current-equity universe, authentication,
  streaming limits, quote recordings, freshness and actual deployment evidence.

- [Phase 2 implementation and validation guide](docs/phase-2.md) for current artifacts,
  explicit capability limits, operational behavior and owner validation.

1. [Phase 2 plan](docs/phase-2-plan.md) — complete research/portfolio simulation plan,
   strategy universe, data requirements, milestones 2A–2F and acceptance evidence.
2. [Product charter and roadmap](docs/product-plan.md) — phase boundaries and governance.
3. [Current architecture](docs/architecture.md).
4. [Phase 1 delivery record](docs/phase-1.md) — implemented behavior and validation limits.

The Phase 2 plan is a planning baseline, not an implementation-status claim. At its
creation on 2026-11-04, milestones 2A–2F were not started. Current implementation evidence
is now recorded in docs/phase-2.md. Inspect current code and durable
milestone evidence before choosing the next task. Update the plan with artifact and
validation references as work progresses.

- [Manual real trading](docs/manual-trading.md): manual NSE delivery authority is independent from strategy qualification. The owner requested real trading; the server connection can be enabled only by the validated activation tool. No allocation, account mandate, static-IP assertion, session arming or individual order is implied. Derivatives and automated strategy execution remain unavailable.

## Owner direction and boundaries

- Development budget is flexible. Support a broad strategy universe across stocks,
  stock swing/positional and intraday trading, futures, options and portfolio strategies.
- Strategy capabilities depend on suitable data, accounting, margin and execution models.
  Keep unsupported capabilities and approximations explicit.
- Initial India/NSE and Zerodha scope is an assumption to confirm; retain extensibility.
- Phase 2 remains paper/research only. AI candidate creation is Phase 3, full forward AI
  supervision is Phase 4, and live trading requires the later explicit enablement gate.
- Flexible budget does not authorize spending or enabling broker orders.

## Engineering invariants

- Manual, algorithmic and future AI intentions go through shared independent risk checks.
- Preserve atomic account, cash-ledger, fill, journal and decision evidence updates.
- Monetary cash/basis values use integer paise internally. Preserve unit clarity in APIs.
- Historical replay needs its own clock and information cutoff; prohibit future access.
- Research workers use isolated accounts/databases and cannot alter the main paper account.
- Before future source changes, preserve the current complete app source and dependency locks under `data/releases/{code_hash}/`, verify the hash, and retain the build referenced by existing candidate evaluations. Archives must exclude credentials and account data.
- Preserve existing data and audit history during migrations. Use SQLite's backup API for
  consistent backups; `tools/backup.py` provides the existing backup command.
- Keep credentials on the server and out of logs, browser responses and documents.
- Onboarding accepts Kite app keys through the local same-origin UI, stores them atomically in owner-only `.env.local`, and never reads them back to the browser. Persisted file values take precedence over environment values. Validation errors for credential routes must remain redacted.
- Phase 3 AI uses dedicated ARTHA_AI_API_KEY / ARTHA_AI_MODEL configuration. Never reuse agent credentials, expose holdout observations to the model, auto-retry uncertain provider calls, or execute generated source code.
- Candidate versions and study protocols are immutable. Finalist selection consumes the holdout; paper approval creates a packet only. Maintain trial budgets, failed attempts, replay hashes and the local audit chain.
- Recheck changing exchange/broker rules against primary sources and preserve effective dates.

## Repository map

- `app/team/customer.py`, `app/team/customer_cohort.py`, `static/team/customer.{js,css}` and `tools/customer_cohort.py`: MVP-5 Customer pilot records exact research cycles, cumulative independent-review timers, precommitted operator observation journals, effort, integration demand and admin-only commercial evidence. It creates no engine/forward/broker authority. Preserve CONTROLLED_EXERCISE exclusion, role-filtered exact exports, integer-paise money, unique references, audited entry corrections, actual elapsed bounds and organization-level deduplication. Backups verify the new tables; restored generations and changed builds invalidate prior observation plans. Reports never independently certify customers/payments or complete external acceptance. See tests/test_customer_operations.py and tools/customer_browser.py.

- `app/validation/`, `tools/validate_financial.py`, `static/team/validation.{js,css}`: MVP-4 fictional financial goldens and build-bound software evidence. Owner and authenticated team views are read-only; downloads preserve JSON numeric types and the report hash. Expected values must remain separate from tested outputs. Preserve full case inventory, failed cases, current source/build checks and explicit external acceptance gaps. No report grants broker or strategy authority. See `tests/test_financial_validation.py`, `tools/financial_validation_browser.py` and the MVP-4 guide.

- `app/team/`, `static/team/`, `tools/team_{admin,ops,server,browser}.py`: MVP-3 account-based identity/roles, isolated organization research, immutable contributor-aware reviews, bounded paper-research scopes and private verified recovery. A single organization has its own store/listener/OS account. Never expose the local-owner application's legacy APIs as a commercial team service. Sessions derive identity server-side; worker authority stays outside economic configuration but binds to attempt/run metadata. Recheck scope, identity, recovery generation and exact inputs during execution. Preserve distinct snapshot acceptance versus operating permission; never treat either as broker/forward qualification. Backups lock authority mutations across processes and pause workers, omit live sessions/invites, preserve source/data, and restore only to a new directory with renewed scope. Actual off-host copying requires an operator-supplied SSH target and pinned GPG recipient; do not infer one. See the MVP-3 guide and meaningful security/recovery/browser cases.

- `app/contract_research.py`, `app/research/contract_replay.py`, `static/contract-research.js`: MVP-2 immutable protocols, isolated shared-engine trials, receipt/cutoff clocks and private evidence packets. Baseline/reproduction/stress attempts retain failures and consume a frozen budget; dispatch recovery reuses deterministic run IDs. Preserve old records/build archives. Summary exports omit quote-bearing traces; trace export requires explicit permitted-use acknowledgment. Hash checks do not authenticate data or grant broker/team authority. `tests/test_contract_research.py`, `tools/contract_research_browser.py`, `tools/verify_contract_packet.py` and all-store restore checks cover this work. See the MVP-2 guide for exact bounds and pending external acceptance.

- `app/contracts.py`, `static/contracts.js`, `static/contracts.css`: separate MVP-1 definitions, integer-paise bounded payoff, immutable revisions and actual dataset compatibility. Receipt-time provenance and commercial rights remain unverified; definition checks do not execute research or orders. `tests/test_contracts.py` and `tools/contracts_browser.py` cover the new flow; workspace backup checks include the new tables.

- `app/workbench/templates.py`: structure classifications and normalized recipe-derived payoff previews. Single-expiry profiles use contractual intrinsic value; calendar/diagonal previews retain later-expiry model value. These are illustrative shapes, never live prices or market forecasts. The visible template library filters independently from the active basket.


- `app/workbench/studio.py`: contract-verified analysis adjustments, audited cached-position imports, immutable quote tracking and explicit read-only basket margin calculations. No derivative order dispatch. New mark rows require backup/read integrity checks. Disabled legs must stay excluded from handoffs while retaining their original historical mapping indices. Manual P&L stays an analysis offset. [Builder comparison](docs/strategy-builder-parity.md) documents real capability boundaries.


- `app/pilot/limits.py`: explicit BROKER_FUNDS mode has null app financial and daily-entry budgets; CUSTOM preserves positive owner-defined limits, without hard-coded amount/count ceilings. Legacy saved mandates remain CUSTOM. Never replace missing custom fields with inferred cash amounts or infinity. Current broker funds, pending reservations, quotes, confirmations and native protection remain independent execution checks.

- `app/pilot/universe.py`: all-stock authority applies only to manual NSE one-unit delivery equities/ETFs, validated against the current master at execution. Indices and derivatives remain excluded. Legacy mandates default to their selected symbols; strategy-linked authority cannot request an all-stock universe. Preserve shared account-wide budgets, owned-stock exits and quote checks.

- `app/pilot/onboarding.py`: side-effect-free manual setup preflight and field-level errors. Verified setup may persist for the authenticated session/day; execution snapshot freshness still expires after 15 seconds. Never infer a capital allocation or mark static IP / delivery authorization as broker-verified from a successful account read. The four-stage onboarding distinguishes owner confirmations from API verification.

- `app/portfolio.py`: pure broker portfolio presentation, integer paise totals, explicit T+1/MTF/position scopes. Portfolio fixtures and screenshots must never contain private owner holdings. The audited `portfolio_snapshot` cache is display-only; the separate execution snapshot must still clear on restart. Recovery may restore only a matching display cache from a verified backup.
- `app/displaybars.py`, `app/workbench/`: persistent named watchlists and same-origin streamed display snapshots. Developing bars are partial display observations; preserve independent execution quote consumption.
- `app/charts.py`, `static/charts.js`: persistent display-only completed broker candles; never execution liquidity.
- `app/pilot/`, `static/pilot.js`: separate real-money mandate/reconciliation/order journal. The market-data allowlist and paper adapters remain restricted. `ARTHA_LIVE_TRANSPORT_ENABLED` defaults off and requires explicit reviewed deployment authority.
- `tests/test_pilot_charts.py`, `tools/pilot_charts_browser.py`: isolated controlled acceptance; `docs/phase-5.md` records current scope and remaining work.


- `app/intelligence/`: immutable dated inputs, fixed official-source adapters, source jobs and estimated Kite surfaces. `static/inputs.js`, `tests/test_intelligence.py` and `tools/inputs_browser.py` cover the operator flow.
- Selected external features require the immutable `evidence_policy_hash`. Preserve first-receipt cutoffs, unknown publication times, source scope/base years and old revisions. Never scrape restricted public websites, backfill later-known facts into earlier decisions, or silently relabel derived IV as broker-provided Greeks.

- `app/forward/`: immutable forward books, shared financial engine, independent supervision, advisory AI, durable incidents/notifications and isolated drills.
- `static/forward.js`, `tests/test_forward.py`, `tools/forward_browser.py`: forward UI and controlled acceptance.
- `app/operations.py`, `tools/forward_ops.py`, `tools/archive_release.py`, `deploy/`: watchdog, all-store backup/restore checking, source preservation and managed units.

- `app/configuration.py`, `app/onboarding.py`, `static/onboarding.js`: private credential persistence, setup status/data checks and guided UI.
- `tests/test_onboarding.py`, `tools/onboarding_browser.py`: secret-redaction, OAuth and connection acceptance.
- `app/candidates/`: immutable studies/versions, bounded Responses connector, evaluation orchestration, holdout protection and paper review packets.
- `static/candidates.js`, `tools/candidates_browser.py`, `tests/test_candidates.py`: Phase 3 UI and controlled acceptance.
- `app/engine.py`: account, order lifecycle, risk, persistence, decision evidence.
- `app/execution.py`: execution proposal contract and installed paper simulator.
- `app/strategies.py`: current pure versioned trend rules.
- `app/finance.py`: money, estimated charges and option calculations.
- `app/market.py`: fixed-allowlist Kite market data/authentication and cached instrument master.
- `app/live.py`: all-NSE-equity stream hub, paginated scanner and isolated quote captures.
- `static/market-data.js`, `tools/market_browser.py`, `tests/test_live_data.py`: market-data UI and controlled acceptance.
- `app/api.py`: validated local API; `static/`: browser UI; `run.py`: local launcher.
- `tests/`: engine/API/Phase 1 acceptance checks.
- `tools/browser_smoke.py`: isolated browser checks and screenshot artifacts.
- `app/research/`: contracts, catalog, strategies, replay, process jobs, metrics and APIs.
- `app/risk.py`, `app/charges.py`: shared budget/cash-flow and charge calculations.
- `static/research.js`: Research lab UI.
- `tools/research_browser.py`, `tests/test_research.py`: research acceptance checks.
- `docs/`: product plans, architecture, delivery records and validation evidence.

## Validation and local operation

Runtime is Python 3.13. `requirements.lock` and `requirements-dev.lock` pin the recorded
environments. Existing checks from the project directory:

```bash
.venv/bin/python -B tools/validate_workbench.py
.venv/bin/python -B tools/workbench_deployed_browser.py
```

The first command runs full pytest, the MVP-4 financial benchmark and ten boundary gates, fifteen isolated browser suites, dependency checks and all
JavaScript syntax checks, recording exact source/asset fingerprints. The second is a
read-only deployed UI check. `--resume` is restricted to browser-harness changes with
unchanged runtime, assets, unit tests and dependency locks; reused passed unit evidence
retains its original validation timestamp. Legacy `browser_smoke.py`, `market_browser.py`
and `pilot_charts_browser.py` describe earlier navigation and are retained for those releases.


Use checks appropriate to the change. Documentation-only edits need link/content checks;
application changes need relevant regression and acceptance coverage. The current workspace browser tool
uses a temporary database and port 8796. The normal launcher uses loopback port 8787 and
a single-process lock. Update validation artifacts only from actual check outcomes.
