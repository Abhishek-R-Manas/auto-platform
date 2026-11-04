# Phase 1 delivery record

## Implemented

- Browser workspace with overview, market watch, positions, order blotter, strategies, risk/capital, journal and settings.
- Synthetic quote playback with explicit virtual clock, manual stepping and persistent quote observations.
- Virtual capital adjustment, risk-based sizing, minimum-lot eligibility and archived research sessions.
- Manual market/limit simulations; partial fills; pending funding and risk reservations; idempotent submissions; cancellations; reduce-only exits.
- Protective stops/targets, options intraday controls and derivative expiry cutoffs.
- Long delivery equities, long calls/puts, and long/short futures, including simulated daily MTM.
- Options IV/Greeks, per-underlying delta and joint spot/volatility/time stress estimates.
- Independent account loss, drawdown, funding and margin controls; negative-equity gap-loss accounting.
- SQLite cash reconciliation, transactionally committed fills/journal/quotes, restart readiness pause and process ownership.
- Deterministic trend-crossover baseline using the same checks as manual entries.
- Optional server-side Kite authentication and current quote/master ingestion through a fixed market-data endpoint allowlist.
- CSV journal export, notes, desktop/mobile layouts and keyboard-accessible order entry.
- Durable decision history with strategy/configuration versions, known market and portfolio inputs, sizing explanations, individual risk check outcomes and linked fill evidence.
- Separate pure strategy rules, execution proposal interface and enforced paper adapter.
- Per-leg option expiry, theta/Greeks, spread/depth monitoring and combined held-leg exposure by underlying.
- Realised net versus unrealised P&L, optional manual trade rationale, historical instrument metadata, dependency locks and a consistent SQLite backup tool.
- Product charter, phased roadmap, risk policy, data governance and measurable release criteria in `product-plan.md`.

## Important scope boundaries

Phase 1 is a local paper-trading foundation. Options writing and multi-leg execution are not enabled. Exact exchange queues, calibrated margin, corporate actions, actual exercise/physical settlement and live portfolio reconciliation are not implemented. Current fees and margins are explicitly estimates.

Historical backtesting is Phase 2; AI candidate generation is Phase 3. The baseline and synthetic results do not establish profitability. AI monitoring is not simulated with fabricated model outputs.

No actual Kite account, subscription permissions or broker market session has been tested without your credentials. The default account is entirely synthetic. Connecting Kite quotes cannot place live broker orders.

## Verification evidence

Validated on 2026-11-04 after the Phase 1 completion changes:

- `pytest -q`: **62 passed**, no failures. One dependency deprecation warning from Starlette's TestClient/httpx integration.
- `node --check static/app.js`: passed.
- `pip check`: passed; no broken installed requirements.
- Browser acceptance: **14 workflows passed**, no JavaScript errors; desktop/mobile and decision/options screenshots saved under `screenshots/`.
- Accounting, audit, migration, backup/recovery and browser checks used isolated databases. The main paper account was not opened or changed.

Machine-readable results: `validation.json` and `browser-validation.json`.


`tests/test_phase1.py` adds decision persistence/immutability, atomic rollback, archive/cursor behavior, reproducible strategy signals, manual rationale, cross-leg exposure, invalid-quote rollback, controlled Kite ingestion, legacy schema upgrade, instrument history, and WAL-safe backup/restart checks.

`tests/test_trading.py` covers cash and position accounting, idempotency/concurrency, risk rejection, pending reservations, stops while paused, limits, partial-fill fees, futures shorts, naked-short rejection, stale data, restart recovery, capital-based sizing, capital controls, option calculations, futures gap insolvency, risk exits, joint stress, source-change races and session archives.

`tests/test_api.py` covers browser/API state, strict validation, same-origin mutation controls, rejection parity, pause/close flows, spreadsheet-safe journal export, forged OAuth callbacks, disconnected Kite behaviour and live-order endpoint denial.

`tools/browser_smoke.py` uses a separate database for actual browser checks and writes `docs/browser-validation.json` plus screenshots. Main-account data is unaffected.

Security guidance applied: CodeGuard credential, cryptography/certificate, API, validation, storage, browser, privacy and dependency rules. No real credentials or certificates were added; outbound TLS validation remains enabled. No independent security audit or real-money readiness claim is made.

No Git commands or external publication were used. All application artifacts are in `/root/amanas/temp/auto-platform`.


## Completion scope

The Phase 1 implementation covers the agreed paper-platform acceptance baseline in
`product-plan.md`. Cross-leg options monitoring aggregates independently executed positions;
the original plan does not require linked multi-leg execution. The backend installs and
enforces the paper adapter, with no live order routing. Strategy rules and risk calculations
have reusable boundaries for future reviewed work.

The authenticated Kite account/subscription and live-session validation remains unperformed
without user-provided server credentials. Earlier orders retain their journal; evidence for
new decisions begins with this implementation. The local decision store is not tamper-evident.
The runtime and validation dependencies are pinned for Python 3.13 Linux; fresh dependency
downloads are separate from checks using the installed environment.
