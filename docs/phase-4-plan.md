# Phase 4: supervised forward paper portfolios

Planning baseline: 2026-11-04. Real-money execution remains disabled. This plan defines
software acceptance separately from strategy qualification, provider validation and long-term
operating evidence. Implementation status and measured release evidence belong in phase-4.md.

## Scope and immutable boundaries

Run predeclared stock intraday/swing, futures and options strategies through a shared,
independent paper risk/accounting boundary. Reuse Phase 2 pure signal rules, financial
calculations and execution simulator. Never execute generated source or broker orders.
Keep all portfolios isolated from the existing paper desk and research accounts. Candidate
approval remains an immutable evidence packet; explicit Phase 4 activation is separate.
Operator reference experiments remain labelled unqualified and cannot become Phase 5 packets.

Every portfolio fixes capital, strategy versions, instrument bindings, session calendar,
bar frequency, risk policy, execution estimates, qualification thresholds and reviewer.
Changed economics require a new portfolio version. Every decision identifies known data,
version/build, sizing, risk results, observed liquidity and eventual simulated executions.

## 4A — Provider and data readiness

- Preserve the existing authenticated Kite connection and server-only credentials.
- Verify current token/contract identity, lot size, tick size, expiry and underlying references.
- Explicit calendar validity and review, exchange/receipt timestamps, fresh executable depth,
  completed-bar formation, bounded warm-up and declared gaps. Never fabricate missing bars.
- Independent access, transport, freshness and usable strategy coverage states.
- Private dedicated AI configuration; missing credentials remain an honest disabled state.

## 4B — Continuous forward execution

- Approved registry candidate/packet activation with immutable provenance and current-state
  checks. Unsupported families, expired contract bindings and incompatible bar semantics block.
- Explicit unqualified reference portfolios for controlled experiments and owner evaluation.
- Shared capital across strategies, FIFO positions, paise accounting, costs, margin estimates,
  partial fills, cancellation, expiry/roll controls, per-leg exposure and unresolved obligations.
- Signals only on completed bars; executions only from fresh real observations at receipt time.
  Pre-decision history never supplies executable depth. Duplicate/out-of-order data cannot
  duplicate signals or replenish an unchanged quote's consumed liquidity.
- Persist checkpoints, input watermarks, orders, fills, ledger and decision evidence atomically.
- Pause/resume, cancel entries, reduce-only liquidation requests and retirement; an exit request
  is never represented as a completed flatten when liquidity is unavailable.

## 4C — Independent portfolio risk

- Trade/open/daily/weekly/drawdown, funding, allocation, gross and underlying exposure controls.
- Options sensitivity, gap/volatility/time-decay, liquidity, margin and correlation stress views.
- Health failures block entries; risk controls remain active when AI is missing or fails.
- Conservative gross margin and estimated charges/Greeks stay visible; no exact SPAN claim.
- Explicit overnight, session, contract-expiry and missing reference/settlement policies.

## 4D — Supervision and recovery

- Deterministic supervisor: stale/missing feed, session expiry, data gaps, revoked candidate,
  reconciliation, stalled workers, storage, errors and failed model calls.
- Durable deduplicated incidents, acknowledgement/resolution with evidence, operator runbooks,
  local notification outbox and escalation status. External delivery needs configured targets
  and explicit authorization; do not send unsolicited messages to anyone.
- Restart paused, replay-safe event watermarks, compatible-build checks and an independent
  process watchdog. Backups include all relevant databases; validate isolated restoration.
- Fault drills for feed loss, stale depth, provider failure, process restart, duplicate input,
  partial legs, missing settlement and accounting failure. Never inject faults into owner books.
- Managed single-process loopback service, health probes, bounded resources and recovery guide.
  This is single-host operations, not a high-availability or multi-user service claim.

## 4E — Bounded AI monitoring

- Evidence-only explanations and proposed interventions over bounded forward portfolio facts.
- No tools, broker authority, risk override, strategy editing, self-approval or holdout feedback.
- Durable explicit/opt-in periodic request budgets, output reservations, usage, model/prompt/
  evidence hashes, failures and uncertain-call handling. No automatic retry of uncertain calls.
- Provider outage cannot stop deterministic supervision. AI advice never directly executes.
- A human may request only existing deterministic controls; rejected advice remains retained.

## 4F — Forward qualification and operator experience

- Forward workspace: portfolios, current risk, positions/orders/fills, completed bars,
  benchmark-relative prospective performance, incidents, AI notes, interventions and evidence.
- Predeclared minimum sessions/fills, after-cost performance, drawdown, execution/data coverage,
  stress and recovery requirements. Preserve unsuccessful trials and all configuration changes.
- Export review packets with actual elapsed evidence and limitations. Reference/synthetic
  experiments, unresolved incidents, unreviewed data, failed drills or inadequate samples block.
- Phase 5 remains a separately authorized supervised live pilot. No Phase 4 UI/API can enable it.

## Acceptance evidence

Focused unit/integration tests plus real browser journeys cover lifecycle, candidate integrity,
bar cutoff, no-lookahead, idempotent observations, partial fills, portfolio risk, expiry,
accounting rollback/reconciliation, restart, isolated backup/restore, AI bounds/redaction,
incident acknowledgement, report/export and mobile usability. Existing suites must still pass.
Record real Kite connectivity separately from controlled quotes and model responses. Actual
AI account validation and sufficient prospective strategy evidence are required before claiming
a qualified continuously supervised strategy. Software tests cannot establish profitability,
perfect reliability, all possible strategy support or an industry-leading certification.
