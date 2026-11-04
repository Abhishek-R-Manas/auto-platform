# Phase 4 implementation and operator guide

Release 0.5.2, audited and corrected 2026-11-04. The software delivers supervised forward **paper**
portfolios. Actual prospective qualification takes elapsed market sessions; software tests
cannot manufacture those sessions or establish an investment edge. The only installed
execution adapter is the simulator. No API, AI action or UI control enables live orders.

The subsequent [end-to-end implementation audit](phase-4-audit.md) records corrections to
job dispatch, candle supervision, continuous options risk, guardian review and deployment.

## What is delivered

| Milestone | Implementation | Evidence |
|---|---|---|
| 4A — Provider/data readiness | Current-master bindings, both quote clocks, completed broker candles, private dedicated AI setup | Forward/onboarding tests; separate real Kite connection record |
| 4B — Forward portfolios | Immutable registry/reference experiments, shared strategy/risk/accounting, partial legs, daily next-session signals, atomic checkpoints | Runtime/service tests and forward browser journey |
| 4C — Independent risk | Funding/allocation/gross exposure, loss/drawdown, spread, Greeks, correlation, expiry/intraday exits and stress grid | Controlled equity/futures/options and risk tests |
| 4D — Recovery/operations | Paused restart, durable incidents/outbox, isolated drills, watchdog service, online backups and restore checking | Drill results, operations tests and deployment record |
| 4E — Bounded AI | Structured advisory notes, evidence citations, explicit periodic consent, durable output budgets, no uncertain-call auto-retry | Controlled provider/browser acceptance; genuine model validation remains separate |
| 4F — Qualification/UX | Forward dashboard, benchmark/performance, traceable events, interventions, prospective gates and evidence exports | Browser/mobile acceptance and report tests |

Measured validation and deployment outcomes are recorded in `phase-4-validation.json`,
`phase-4-browser-validation.json` and `phase-4-deployment.json`. The commercial direction is
[Centaur Strategy Forge](commercial-strategy.md); its business stages do not replace these
engineering release gates.

## Start a forward experiment

1. Open `http://127.0.0.1:8787/#setup` through the existing SSH tunnel. Saved Kite keys remain
   private. Complete normal Zerodha sign-in, verify quote/candle access and start the NSE feed.
   A server restart discards the memory-only daily session token; sign in again. A successful
   access sample after hours does not mean that executable prices are fresh.
2. Open `/#forward` → **New experiment**. Choose an approved registry candidate or an explicitly
   unqualified reference strategy. Registry activation verifies the current immutable paper
   packet, audit chain, strategy, data review, historical frequency and policy. It rejects
   weaker risk, improved execution assumptions, silent fixed-asset substitution and revoked
   approvals. A different execution build requires a documented compatibility review.
3. Set virtual capital and exact current NSE/NFO bindings. Underlying references are added for
   derivatives. Review the current master, whole lots, calendar, dated corporate actions,
   rights, execution estimates, reviewer and benchmark. The default 2026 holiday baseline is
   valid only through December 31, 2026. Special sessions require a reviewed calendar override;
   there is no automatic exchange calendar or corporate-action service.
4. Create the immutable experiment. It starts **PAUSED**. Changing economics, terms, strategy,
   calendar or qualification thresholds requires a new experiment. The advanced JSON editor
   accepts multiple approved candidates and the supported declarative strategy families;
   arbitrary generated Python or unsupported market microstructure is not executable.
5. Load completed-candle warm-up before advancing the book. Warm-up supplies only history:
   it creates no executable quotes, signals, orders or fills. Learned references freeze their
   model before activation; registry learned models retain their approved training cutoff.
6. Review readiness and explicitly **Resume**. Completed bars produce signals; only subsequent
   fresh quotes can fill them. Daily swing entries wait for the next eligible session's fresh
   observations within the declared window (default 120 seconds). Late missed signals are
   discarded. Backfilled missed bars update history without replaying old trading decisions.
7. Inspect positions, pending orders, event evidence, stress, benchmark-relative results and
   incidents. Pause cancels entries but retains protective exits. **Liquidate** creates actual
   reduce-only requests; thin or stale depth can leave exposure pending. **Retire** requires a
   flat book and no pending orders. The original paper desk is a separate account and source.

## Execution and accounting contracts

The forward runtime reuses the historical engine's pure signals, sizing, independent risk,
FIFO lots, integer-paise ledger, charges, order lifecycle and paper execution proposals.
It uses a real event clock rather than accelerating production market time. Exchange and
receipt age must both pass the protocol's maximum (at most 30 seconds), including underlying
references. Duplicate/out-of-order observations cannot refill consumed depth. Participation
limits apply to displayed bid/ask depth, and hedge exits wait for actual short-leg buybacks.

Entries have both completed-bar and wall-clock TTLs. A stalled candle stream cannot keep an
old entry alive indefinitely. Reduce-only exit requests do not expire under the entry TTL.
Session boundaries cancel old DAY entries; intraday and expiry buffers request exits.
Futures daily MTM uses an explicitly estimated near-close quote once per lot/session, when
available. No official settlement, physical delivery or fabricated expiry cash flow is
invented. Unresolved expired holdings remain visible obligations and block new exposure.

Margins are conservative gross estimates, not broker SPAN/portfolio-margin confirmations.
Fees, European Greeks/IV, spot/IV/time-decay scenarios and historical correlations are model
estimates. Unknown correlations are grouped conservatively. Stops and risk budgets cannot
guarantee fills, exit prices or maximum losses. Supplied corporate actions can be applied by
the shared engine; automatic complete corporate-action sourcing remains additional work.

Protocol, checkpoint, orders/ledger changes and SHA-256 chained events commit together in
`data/forward/forward.sqlite3` (SQLite WAL/FULL). This local chain detects changes; it has no
external trust anchor. Changed execution builds block resume rather than silently running
old experiments under new code. Existing source archives remain available for provenance.

## AI monitoring and human controls

In **AI & operations**, save a dedicated application API key and an explicitly supported
structured-output model name. Fields clear after saving and keys are never read back to the
browser. A genuine successful **Request AI review** is still needed to verify account/model
access. No agent credentials are reused. Provider usage can incur charges.

AI receives a bounded snapshot of forward facts, with exact evidence identifiers. It uses
fixed-origin Responses Structured Outputs, no tools, no broker authority, no holdout feedback
and no generated-source execution. Citations outside the supplied snapshot reject the note.
Notes propose only existing human controls; they never execute a control, edit a strategy,
approve themselves or weaken deterministic risk. Provider/model/prompt/input hashes, usage,
failures and uncertain outcomes remain durable.

Periodic requests are disabled initially. Enabling requires explicit usage consent. Defaults:
7,200-second interval, 12 requests/day, 1,500 maximum output tokens/request, 18,000 lifetime
reserved output tokens. Reservations are conservative and are not returned after uncertain
calls. These are output limits, not a currency or total-input-token spending cap. Failed or
uncertain monitoring suspends periodic requests; review the cause and save an authorized
policy to resume. Cancel/restart does not silently retry a paid request. Deterministic risk
and supervision continue without AI. Continuous operation means the service remains running;
it does not imply markets trade 24/7 or that an expired broker session renews itself.

The human guardian is currently the authenticated OS/workspace owner. Reviewer text fields
are operator assertions, not verified professional licensing, separate-person approval or
customer suitability. Multi-user roles and commercial regulatory workflows are later work.

## Incidents and optional notifications

Transport, stale quotes, invalid terms, revoked approval, model failures, accounting/halt and
unresolved obligations create deduplicated durable incidents. Acknowledgement records the
owner's review; it does not clear an active risk condition. Resolution requires the underlying
condition to clear. Review the event trail, fix connectivity/data/configuration, verify
reconciliation, then explicitly resume. Never treat a liquidation request as confirmation
that the position is flat.

The dashboard outbox works without external services. Optional Telegram delivery is disabled
until private bot token/numeric chat id, explicit enablement and consent are saved. Create a
bot with [BotFather](https://core.telegram.org/bots/features#botfather), start its chat and
obtain the intended numeric destination privately. Only authorize a destination you control.
No real notification credentials or live delivery were supplied for this release validation.

Messages contain portfolio/incident identifiers, severity and a dashboard-review instruction;
no keys, balances or holdings are sent. The default delivery limit is 50/day. An attempt is
committed before HTTP; unknown/failed delivery has no automatic retry. Explicit retry of an
uncertain send requires acknowledgement that it may duplicate a message. Confirmed messages
are not retried. Controlled delivery tests do not establish actual reachability of your chat.

## Managed service, backups and recovery

Deployment installs `deploy/artha.service` as the single loopback process, with systemd
readiness/watchdog, restart-on-failure, bounded memory/tasks and restricted filesystem/service
settings. It currently runs as the existing root workspace owner; it is not a public hardened
multi-tenant service or a high-availability deployment. Do not expose port 8787 publicly.

```bash
systemctl status artha.service
.venv/bin/python tools/forward_ops.py health
systemctl status artha-backup.timer
.venv/bin/python tools/forward_ops.py backup
.venv/bin/python tools/forward_ops.py restore-check data/backups/EXACT_SNAPSHOT_DIRECTORY
```

The daily backup timer runs around 02:30 Asia/Kolkata. Snapshots include relevant SQLite
stores, immutable research datasets and source releases, with manifest hashes and integrity
checks. Secrets are excluded; preserve private configuration separately through an appropriate
secure owner process. Each SQLite snapshot is consistent. Pause mutations/jobs for an exact
cross-database instant. Local backups share the host's failure domain, have no automatic
retention/offsite replication, and need storage review. Large datasets increase backup cost.

On restart all forward books pause and uncertain jobs are marked interrupted. Inspect audit,
orders, positions and incidents, restore fresh broker connectivity and resume explicitly.
**Run recovery drills** creates isolated controlled books and an online read-only owner backup;
it never injects faults into owner accounts. Drills cover partial fills, duplicate/stale/future
observations, unresolved exits, transaction rollback, paused restart, AI absence and restoration.
Actual recovery by replacement is a separate deliberate operation: stop the service, preserve
current data, validate the exact snapshot in isolation, restore a mutually consistent set and
restart paused. The restore-check command never overwrites production data.

## Capacity, qualification and remaining evidence

Operating ceilings: 200 archived experiments, eight concurrent running portfolios, 50 bound
instruments/book including underlying references, 500 selected forward tokens globally,
16 pending jobs, one worker per candle/warm-up/AI/notification type, 2,048 history bars per
instrument and a declared aggregate 30 candle requests/minute demand budget. Shared historical
requests are centrally paced below Kite's published three/second ceiling. Quote-sample bars
are approximate and allowed only for unqualified references. These are protective limits,
not measured throughput/SLA claims. Account snapshots/event reports still require scale and
retention work before sustained high-volume or many-user operation.

Qualification defaults require at least 20 actual healthy observed market sessions (each
at least 60 healthy minutes), ten completed orders, a predeclared benchmark, after-cost return,
drawdown/coverage limits, intact accounting/audit, no unresolved incidents/obligations, passed
recovery drills and genuine AI monitoring validation. A sparse single quote cannot count as
a session. All gates are reported explicitly. Reference experiments never qualify. Even a
passing report means **eligible for Phase 5 review**, not live permission or proven alpha.

Genuine Kite stock quote/history sample access and streaming connection were observed before
this deployment; see `kite-connection-validation.json`. That does not prove every symbol,
NFO segment, depth, historical period or data-use license. Genuine model/Telegram credentials,
actual prospective sessions, an options-chain/skew/FII/DII/macro source, official margin/
settlement feeds, external security/recovery review, offsite backup and commercial identity/
regulatory readiness remain separate dependencies. No strategy has been declared qualified
from controlled tests or the implementation date alone.

## Validation commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python tools/forward_browser.py
.venv/bin/python tools/browser_smoke.py
.venv/bin/python tools/research_browser.py
.venv/bin/python tools/market_browser.py
.venv/bin/python tools/candidates_browser.py
.venv/bin/python tools/onboarding_browser.py
.venv/bin/python -m pip check
node --check static/forward.js
```

Controlled provider/fault fixtures are labelled in their artifacts. Security validation covers
same-origin mutations, bounded/redacted credential routes, server-only atomic private config,
fixed provider origins, no external redirects/proxies, structured input/output, no tools/live
adapter, isolated workers/accounts and secret-free operational output. Kite streaming retains
its previously documented TLS 1.2+ compatibility exception; other new provider clients use
verified TLS 1.3+. These controls were checked directly during the implementation audit; no independent security certification is claimed.


## Guardian evidence review and audit corrections

In the forward portfolio, inspect qualification, positions, pending orders, scenarios and
incidents. Select **Capture displayed evidence for review**, enter the reviewer, findings,
conflicts/authority assertion and a validity of one to 30 days, then record the decision.
Continue-paper, rejection and revocation decisions are retained. Approval for later pilot
review requires every technical gate to pass. It never enables execution. A changed evidence
head, financial report, expiry, rejection or revocation invalidates the current approval.
Guardian events retain their actual review time without advancing the financial clock.
This is still a local owner assertion; professional licensing is not independently verified.

Job reservations and RUNNING markers now commit before any model/candle dispatch. Restart
preserves uncertain reservations without automatically repeating model calls. A running job
exceeding the protocol's worker_timeout_seconds becomes TIMED_OUT; its eventual output is
discarded and its worker slot remains occupied until it actually finishes. Python threads
cannot be forcibly stopped safely. Choose a deadline sufficient for the whole bound-instrument
request batch, including central request pacing. Candle failures/stalls block entries; an
observed successful retrieval clears the condition for explicit incident resolution. Model
recovery works similarly while deterministic controls remain independent.

A required completed candle missing for more than 120 seconds beyond its expected completion
blocks entries even if quotes are fresh. Recovery does not silently resume a paused portfolio.
Pause and restart clear queued next-session daily entry instructions. Options gamma, theta,
vega, correlation and joint-stress budgets are checked on held exposure as well as on entry;
breaches halt entries and request reduce-only exits. Missing depth can still prevent a fill.

Qualification now includes actual benchmark observations, current stress with no unknown
valuations, recovery drills matching the current execution build, and current guardian
approval. Return includes the latest quote mark and maximum drawdown retains observations
between candles prospectively in the corrected runtime. Older unrecorded observations cannot
be reconstructed. Reports, reviews and their event copies are checked during audit and backup
restoration. pytest.ini limits test discovery to active tests, excluding preserved releases.

The operator recovery pack now executes 14 isolated checks, including feed loss, failed model
requests with retained budgets, accounting mismatch, partial option legs and missing official
settlement. Both isolated service instances explicitly disable real model credentials.

## Dated input extension

Release 0.6.0 adds the [research inputs workspace](inputs.md): official daily FII/DII report
imports, automatic MoSPI/RBI public data and estimated Kite option surfaces. New portfolios
may freeze required named inputs and their policy; decision and guardian evidence retain the
associated observation ids. The data-input release has separate validation and deployment
records in inputs-*.json.
