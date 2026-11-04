# Agent guidance for Artha

## Read first

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
.venv/bin/python -m pytest -q
node --check static/app.js
node --check static/research.js
node --check static/market-data.js
node --check static/candidates.js
node --check static/onboarding.js
node --check static/forward.js
node --check static/inputs.js
.venv/bin/python -m pip check
.venv/bin/python tools/browser_smoke.py
.venv/bin/python tools/research_browser.py
.venv/bin/python tools/market_browser.py
.venv/bin/python tools/candidates_browser.py
.venv/bin/python tools/onboarding_browser.py
.venv/bin/python tools/forward_browser.py
.venv/bin/python tools/inputs_browser.py
```

Use checks appropriate to the change. Documentation-only edits need link/content checks;
application changes need relevant regression and acceptance coverage. The browser tool
uses a temporary database and port 8788. The normal launcher uses loopback port 8787 and
a single-process lock. Update validation artifacts only from actual check outcomes.
