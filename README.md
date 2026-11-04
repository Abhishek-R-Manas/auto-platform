# Artha — Centaur Strategy Forge

A local, single-user trading workspace for virtual equities, futures and options.
All orders execute in a simulator. **There is no live broker order adapter or live mode.**

AI-assisted options research with a human guardian, independent risk controls and auditable
prospective evidence. The commercial ambition is compliance-first generative wealth
infrastructure for mass-affluent investors; this release remains a local paper sandbox.

## Phase 4 forward workspace

Open `/#forward` for immutable prospective portfolios, approved-candidate activation, independent risk, advisory AI, incidents and recovery drills. The workspace includes captured-evidence guardian review and visible commercial/data readiness. See [the end-to-end audit](docs/phase-4-audit.md), [the operator guide](docs/phase-4.md) and [commercial strategy](docs/commercial-strategy.md). AI and optional external delivery require dedicated private configuration and actual provider validation. Real-money trading remains unavailable.

The managed deployment uses `systemctl status artha.service`; use the manual launcher below only when that service is stopped. All stores and immutable artifacts are covered by the daily backup timer.

## Research inputs

Open **Forward paper → Research inputs** for official FII/DII CSV imports, automatic MoSPI
CPI/IIP/GDP data, RBI release notices and Kite-based IV/skew. Selected dated inputs can be
required by new forward experiments and added to immutable research datasets without future
information. See [setup, provenance and source limits](docs/inputs.md).

## Run

The isolated environment is already installed in this directory:

```bash
cd /root/amanas/temp/auto-platform
.venv/bin/python run.py
```

Open **http://127.0.0.1:8787**. The process listens on loopback only. Use `--port 8790` to choose another local port. Stop with Ctrl+C. Only one workspace process is allowed.

For a fresh installation with Python 3.13:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

`requirements.lock` pins the runtime dependency graph resolved for this Python 3.13 Linux environment. `requirements.txt` lists the direct runtime dependencies. For the verified development dependency graph, install `requirements-dev.lock`; Chromium additionally requires `.venv/bin/python -m playwright install chromium`.

If this directory is on a remote server, forward the local service from your computer:

```bash
ssh -N -L 8787:127.0.0.1:8787 your-user@your-server
```

Then open the same localhost URL on your computer. This release is not a public, authenticated multi-user deployment.

## First paper session

1. Start with **Synthetic market**. Demo quotes and derivative lot sizes are illustrative, not current market specifications. One tick advances one simulated market minute; automatic playback runs every two seconds.
2. Under **Risk & capital**, select your virtual allocation. The starting value is ₹10 lakh. Allocation changes require a flat account and no pending orders and preserve existing P&L.
3. Open **New paper trade**. Select an instrument, direction, quantity or lots, stop and optional target. Use **Calculate size from risk budget** to compute an eligible quantity.
4. Review funding, estimated charges and entry checks. Place a paper order and follow it in **Orders** and **Positions**.
5. Market orders consume visible simulated bid/ask depth and adverse slippage. Limits wait for eligible quotes; partial remainders remain pending. Stop and target exits are evaluated on quote observations and can execute beyond the intended stop price.
6. Review **Trading journal**, add observations, and export CSV. Historical journal exports include archived sessions.
7. Enable the **Trend crossover** baseline to exercise automated signals. It needs `slow + 1` observed ticks, trades long on bullish crossovers, and exits its own positions on bearish crossovers. It has no established trading edge.
8. Use **Settings → Archive & start fresh** for a new experiment. The account must be flat with no pending orders. Prior results and all ledger entries remain in the database.

Supported positions are long delivery equities, long calls/puts, and long or short futures. Uncovered equity shorts, option writing, multi-leg spreads, corporate-action processing, physical delivery and actual option exercise are not enabled. These restrictions are enforced by the engine and shown in the application.

## Risk and overnight behaviour

These are editable **research defaults**, not optimised investment advice:

| Control | Initial value |
|---|---:|
| Planned per-trade loss | 0.25% of virtual equity |
| Aggregate planned open risk | 0.75% |
| Daily loss threshold | 1% |
| Weekly loss threshold | 2% |
| Drawdown threshold | 5% |
| Futures margin utilisation | 40% |
| Adverse execution slippage | 2 basis points plus tick rounding |
| Illustrative portfolio stress budget | 5% |

Entry checks include whole lots, valid stops/targets, fresh quotes, portfolio risk, pending-order reservations, funding and estimated futures margin. Exits cannot reverse positions. Duplicate client identifiers cannot create duplicate exposure; reuse with different parameters is rejected.

Stocks and futures can carry overnight. Futures have simulated daily MTM using conservative quote marks, not exchange-confirmed settlement prices. Initial options policy is intraday: no entries from 15:15, exit requests from 15:20 IST. Derivative expiry exit requests start at 14:45 on expiry day. Missing liquidity or stale data can prevent an exit; the position remains visible. There is no settlement engine to conceal this exposure.

Loss, margin and funding breaches pause entries, cancel entry remainders and request simulated exits. They do not guarantee an exit price or a maximum loss. Negative virtual cash and equity are recorded honestly if a futures gap exceeds available funds. Restarting restores records, pauses new entries, and retains protective controls. Drawdown/weekly halts require a fresh research session or subsequent policy development; a capital adjustment does not silently erase a halt.

Options Greeks use a European Black–Scholes model with zero dividend yield and a fixed 6.5% annual interest-rate assumption. Demo IV is 20%; Kite IV is inferred from bid/ask midpoints when the model permits it. Theta is rupees per unit per calendar day; vega is rupees per unit per one percentage-point IV change. Portfolio totals multiply by actual units; delta is kept separate by underlying.

Stress estimates jointly reprice positions after ±5% underlying moves, ±10 percentage-point IV changes, and one elapsed day. Worst underlying-group losses are added conservatively. These are illustrative scenarios, not calibrated tail-loss probabilities or maximum-loss guarantees.

## Optional Zerodha market data

Open **Connection setup** (`/#setup`) for the guided ₹500/month Kite Connect flow: save app keys privately, sign in with Zerodha, verify quote/candle access and start the feed. No manual file editing or restart is required for this flow. See [onboarding](docs/onboarding.md).

Kite connectivity is optional. The app runs without credentials. Never paste credentials into chat or commit/share them.

1. Configure a Kite Connect application with the registered callback URL:
   `http://127.0.0.1:8787/api/kite/callback`.
2. Create `.env.local` in this directory using `.env.example` as a guide. Restrict the file with `chmod 600 .env.local`. The launcher rejects broader permissions. Only the documented Kite and Phase 3 AI variables are accepted; this file is parsed as data, never executed as shell code.
3. For manual setup, set `KITE_API_KEY` and `KITE_API_SECRET`, restart the app, and select **Settings → Authenticate with Zerodha**. Alternatively, supply a daily `KITE_ACCESS_TOKEN` server-side.
4. Open **NSE market data → Connect all NSE equities** for the full current equity universe. In Settings, optionally select up to 50 exact stocks/futures/options for the paper watchlist and select **Use Kite quotes** while flat with no pending orders. A blank watchlist retains the default stock/Nifty derivative sample.

The REST connector permits only `GET /instruments`, `GET /quote`, numeric-token historical candle GET routes, and `POST /session/token` at the fixed HTTPS Kite endpoint. Streaming connects only to `wss://ws.kite.trade`. It cannot call broker order endpoints. OAuth tokens stay in server memory and never appear in API responses or database records. Access logging is disabled to avoid logging callback tokens. HTTP redirects are disabled on broker API requests and TLS certificates are verified.

Kite REST quote snapshots are throttled to one request per 1.1 seconds during watchlist setup. Ongoing quotes use full-mode WebSockets plus explicitly labelled REST snapshots for symbols beyond the 9,000-token streaming allowance, covering the complete current NSE equity universe. Public instrument metadata loads before authentication. Paper entries require the configured weekday session, healthy transport, fresh exchange/receipt timestamps and fresh underlying references. A complete independently validated holiday service remains additional work. See [live-data operation and recording](docs/live-data.md).

The app does not read your actual holdings or funds. Paper cash is independent of your Zerodha balance. Margin and fees remain estimates. The connector has been tested with controlled inputs and boundary tests; **genuine stock quote/history sample access and streaming were verified on 2026-11-04; full instrument/segment/history coverage and licensing were not established** (see `docs/kite-connection-validation.json`).

Official references:

- [Kite quotes and instrument metadata](https://kite.trade/docs/connect/v3/market-quotes/)
- [Kite authentication and daily token expiry](https://kite.trade/docs/connect/v3/user/)
- [Zerodha published charges](https://zerodha.com/charges/)
- [Zerodha physical-settlement policy](https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/policy-on-physical-settlement)

## Persistence, privacy and recovery

`data/paper.sqlite3` stores the current account state, cash ledger, fills, journal, quote observations and archived session states. Monetary ledger values use integer paise. Each action commits its state, fills, market observations and ledger/audit entries in one SQLite transaction with WAL and full synchronous durability.

Quote records retain the observed top-of-book snapshot; they are not exchange tick-by-tick history. Chart windows are bounded for rendering, while recorded observations are retained. Watch database growth during extended runs.

The server uses restrictive file creation permissions, explicit request schemas, parameterised SQL, same-origin mutation checks, a Content Security Policy, safe text rendering, spreadsheet-safe CSV output and a fixed outbound API allowlist. It has no portfolio access role or public user authentication. Use it only as a local personal workspace.

Stop the server before copying its database for a straightforward backup. For online backup use SQLite's backup API. Do not copy only the main `.sqlite3` file while ignoring an active WAL. Keep backups private.

## Validation

```bash
.venv/bin/python -m pytest -q
node --check static/app.js
.venv/bin/python -m playwright install chromium
.venv/bin/python tools/browser_smoke.py
```

The browser script starts an isolated server/database on localhost:8788, exercises the workflows, checks narrow-screen layout and JavaScript errors, and writes screenshots and results under `docs/`. It does not use or change the main paper account. Browser binaries use Playwright's configured cache.

See [implementation status](docs/phase-1.md) and [architecture](docs/architecture.md). No Git operations, repository initialisation, commits or remote publication were performed.


## Decision evidence and options monitoring

Trading journal → Decision history records accepted/rejected entries, pending-order waits,
fills, cancellations, sizing calculations, and strategy BUY/EXIT/HOLD/WARMUP/SKIP decisions.
Expand a record to inspect known quotes, strategy implementation version and configuration
hash, risk policy, sizing constraints, risk check results and fill evidence. Complete evidence
includes the available portfolio quotes and positions. Records commit with account changes.
Load older records or include archived sessions; JSON APIs are documented at `/api/docs`.
Earlier journal entries have no reconstructed decision evidence.

Positions → Options legs & liquidity shows each call/put's held units, expiry, remaining
calendar time, spread, executable depth and estimated Greeks. Held-leg Greeks are combined
by underlying. Thin quotes mean fewer than one lot or a spread above 5%; these are monitoring
flags rather than calibrated liquidity scores. Each leg executes independently.
Positions also separates realised net P&L (including costs) and unrealised P&L.

The engine shares pure versioned strategy rules and one authoritative risk evaluation for
manual and algorithmic requests. `app/execution.py` defines the execution proposal interface;
only `PaperExecutionAdapter` is installed and the backend enforces that implementation.
A future broker adapter requires separately reviewed execution/reconciliation work.

## Consistent backup

```bash
.venv/bin/python tools/backup.py /private/existing-directory/artha-backup.sqlite3
```

The command uses SQLite's backup API, includes committed WAL data, checks integrity,
creates a mode-600 destination and refuses to overwrite a file. For recovery, stop the
workspace, retain the existing database and any WAL/SHM files together, and start a separate
instance against a copy of the backup using `ARTHA_DB=/private/restore.sqlite3` and an unused
port. Check reconciliation, positions and journal before resuming entries. Restarts pause
entries automatically. Backup/restart behavior is covered using isolated databases.

See [product charter, roadmap, governance and release criteria](docs/product-plan.md).


## Phase 2 plan and agent handoff

The [complete Phase 2 plan](docs/phase-2-plan.md) preserves the strategy universe,
historical-data requirements, replay architecture, instrument and portfolio models,
research protocol, AI monitoring boundaries and milestones 2A–2F discussed with the owner.
It is the planning baseline for future work; milestone status is tracked inside the plan.

Future agents should begin with [AGENTS.md](AGENTS.md), then read the Phase 2 plan and
[product charter](docs/product-plan.md) before implementation.


## Phase 2 Research lab

Open **Research lab**, or `http://127.0.0.1:8787/#research`, for immutable historical-data
import, capability checks, portfolio replay, isolated jobs, comparison, walk-forward and
cost-sensitivity evaluation, reports and decision inspection.

See the [Phase 2 implementation and owner validation guide](docs/phase-2.md) and the
[full planning baseline](docs/phase-2-plan.md). Research accounts are separate from the main
paper desk. Synthetic fixtures cannot establish historical performance. Review packets
do not activate strategies or enable live trading.


## All NSE equities and streaming data

Open **NSE market data** (`/#marketdata`) for the complete current provider equity universe,
search, pagination, quote freshness and paper-watchlist recording. See [live-data setup and
limits](docs/live-data.md). Genuine prices require a subscribed, authenticated Kite session.
No credentials are bundled, and this release does not claim that a provider session is
already connected. The paper desk remains an independent simulated account.


## Phase 3 Strategy lab

Open **Strategy lab** (`/#candidates`) to predeclare a research study, author or generate
validated strategy specifications, retain immutable revisions and run the evaluation pipeline.
Freeze a finalist for its single-use holdout, record independent data/model review and export
a paper review packet. No registry action enables forward or real-money trading.

AI generation requires dedicated server-side `ARTHA_AI_API_KEY` and `ARTHA_AI_MODEL` values;
the app does not consume coding-agent credentials. Manual authoring remains available when
AI is unconfigured and is labelled separately. See the [Phase 3 guide](docs/phase-3.md) for
setup, study budgets, controls, acceptance evidence and remaining external validation.

Saved `.env.local` entries are authoritative over matching environment variables. The guided
setup preserves existing AI settings and clears any old saved Kite session token on key changes.
Only app keys are remembered; the normal browser-login access token remains in server memory.
