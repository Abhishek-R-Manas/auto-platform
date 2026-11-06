# MVP-2: frozen research and evidence

This is the second stage of the [B2B roadmap](../mvp-validation-roadmap.md), extending
[MVP-1 Strategy Contracts](../mvp-1/README.md). It is separate from the older engineering
Phase 2 and from broker execution. Final check/deployment outcomes are recorded in
[implementation.json](implementation.json).

Engineering implemented and deployed 2026-11-06. Controlled checks passed: 571 automated
tests, 12 isolated browser suites and 26 deployed read-only checks. Customer/source
acceptance remains pending.

## Use the workflow

Open **Strategy Contracts → Research & evidence** at /#contracts. All existing sidebar
options and the original **Define strategy** tab remain available.

1. Save an MVP-1 definition and select an immutable Research lab quote dataset.
   **Load fictional example** explicitly creates a synthetic definition/tape for learning;
   this action never runs automatically on an existing workspace.
2. Review virtual capital, risk limits, observation cutoff, entry window, latency and the
   maximum attempt budget. The organization label records local context, not an authenticated
   organization or a permission boundary.
3. Acknowledge incomplete source/customer evidence when applicable and **Freeze protocol**.
   This pins the exact definition, dataset identity/Parquet hash, engine/dependency build,
   policy, schedule, cutoff and assumptions. Inspect the frozen interpretation in the UI.
4. **Run baseline**, then **Repeat identical inputs**. Both use the same economic configuration,
   seed and engine; each has a distinct durable attempt identity.
5. Optionally **Run cost stress**. This predeclared scenario doubles fee rates/caps, doubles
   slippage with a minimum of 1 bps, and adds one second of latency. It uses the frozen
   source tape, including cases where worse conditions prevent a fill.
6. Failed, cancelled and interrupted attempts remain in the budget/history. An interrupted
   dispatch can recover the same attempt; it does not silently create another run.
   Paused workers can resume from committed checkpoints. A failed protocol attempt is terminal;
   retrying requires a new budgeted attempt so its failure cannot be overwritten through
   either research API. Cancellation stops simulation;
   it does not imply liquidation or a completed valuation.
7. After all attempts stop, **Capture evidence packet**. Inspect the five separate findings:
   data quality, reproducibility, execution realism, research robustness and operational
   readiness. A completed simulation can still have no fills, unresolved exposure or failures.
8. **Export JSON** downloads an aggregate summary by default. Quote-bearing economic traces
   require explicit permitted-private-use acknowledgment; non-synthetic sources also require
   an owner rights assertion/reference in the saved definition. Raw datasets are never included.
9. Verify the original downloaded JSON in the UI or offline. Selecting a protocol in the UI
   checks its expected protocol/build/dataset identities as well as packet integrity.
   A new attempt or engine build makes older packets explicitly historical.

## Supported interpretation

The adapter supports the existing fixed, same-session NIFTY call vertical, put vertical
and iron-condor contract: exact option IDs, equal units, a single scheduled entry and exit.
No dynamic strikes, adjustments, reentry, overnight positions or expiry rolls are inferred.

The adapter subclasses the existing research replay engine. Accounting, execution proposals,
whole-lot/depth constraints, charges, funding, margin, exposure and independent risk limits
remain shared. It adds the following declared basket behavior:

- Timer events evaluate the exact scheduled decision time using already available quotes.
  Timers supply no fabricated market observations and cannot themselves create liquidity.
- Long hedges must fill before short entries can pass shared protection checks.
- Remaining entry orders cancel at the frozen deadline. Partial baskets request an unwind.
- Short buybacks precede releasing the corresponding option hedge. Missing exit liquidity
  leaves simulated exposure visible; it is never replaced with a synthetic closing fill.
- The frozen basket has no implicit per-leg stop/target or default holding-horizon exit.
  Independent account-level risk halts still cancel entries and request exits.
- Current quotes and filled premiums recheck the complete-basket expiry-loss budget before
  entries. This is a contractual full-basket bound, not a guarantee about partial execution,
  intraday marks, gaps, slippage or settlement.

Research jobs own isolated accounts/databases. The main account, saved manual mandate,
cached portfolio, broker arming and order authority are not inputs to these experiments.

## Observation clocks and source contract

The quote manifest accepts observation_clock: EXCHANGE_AND_RECEIPT. Each corresponding
CSV/Parquet row must carry timestamp (exchange event time), received_at (first receipt) and
available_at (usable observation time), with timestamp ≤ received_at ≤ available_at.

Recorded-clock replay orders observations by available_at, filters them at the frozen
cutoff and independently checks exchange age. A delayed old quote cannot become fresh merely
because it arrived later. Mixed receipt-clock coverage is rejected.

Existing coalesced Kite capture CSVs use timestamp for availability, exchange_time for
exchange time and epoch received_at for receipt. The importer recognizes that specific
legacy shape, preserves its original effective timeline and normalizes both additional
clocks. It checks causality without relabeling an old receipt as a new exchange event.

Legacy tapes without arrival fields remain readable. They require acknowledged,
unqualified ASSUMED_EXCHANGE research. Data with a provided arrival clock cannot silently
discard it. Recorded fields demonstrate causal handling of the supplied clocks; their
source authenticity and actual first receipt are not independently authenticated.
The MVP-1 source/customer findings therefore remain visible and unverified.

The adapter requires quote/depth fidelity, compatible identities/dated lot/tick terms,
raw prices, a declared session and dated fee coverage. It accepts at most 50,000 relevant
observations in one session. Freeze requests are bounded at 16 KB; captured packets and
verification inputs at 16 MB. These are local operational bounds.

Use the [synthetic import envelope](examples/quote-import.json) only as a format example.
Its sparse fictional prices, lot size, calendar and illustrative fee schedule are not a
claim about actual exchange history or current official terms.

## Evidence and offline verification

The packet root binds the protocol, all terminal attempts, aggregate results, assumptions,
five findings, dataset/build identities and local research audit head. Each included
economic trace binds fills, marked equity curve and a sequence/hash index of simulation
events. Full event bodies remain in the local run journal.

The event index includes up to 20,000 entries and declares whether it is complete.
A larger journal retains its final event head and a verified prefix; this does not export
every underlying event. Failed/cancelled attempts retain committed partial fills/costs and
their available trace, explicitly without a terminal performance report.

The verifier checks packet/protocol/config hashes, scenario and dataset/build bindings,
included fill counts and event-chain structure, and recomputes completed-run economic
hashes from fills/equity/config/source identity. Reproduction requires all included
baseline/repeat economic hashes to agree. Summary-only exports can pass packet integrity
while trace verification remains false.

Run from the repo's pinned environment, without starting a server:

```bash
.venv/bin/python -B tools/verify_contract_packet.py packet.json   --expected-protocol <known-protocol-id>   --expected-build <known-code-hash>   --expected-dataset <known-dataset-id>   --require-reproduction
```

The expected IDs must come from a separately trusted record if authenticity matters.
The command makes no network/provider calls and never executes source from the packet.
It exits nonzero on invalid evidence or missing requested reproduction.

Hashing uses the repository's Python canonical JSON representation: sorted keys, compact
separators, finite numbers. Preserve the original downloaded file. Tools that rewrite
numeric types (for example 2.0 into 2) alter that representation; the UI downloads/uploads
without numeric reserialization. These hashes are integrity checks, not signatures,
independent data authentication or proof of trading performance.

## Storage and API

New tables mvp_protocols, mvp_trials, mvp_packets, mvp_research_requests and
mvp_research_events live in the existing workspace SQLite store. Records are content
addressed, request IDs bind exact inputs, and audit events form a local hash chain.
A reserved attempt maps deterministically to one research run. Crash recovery around
dispatch reuses that mapping and preserves the attempt budget.

The existing all-store backup/isolated-restore machinery verifies these records, references,
scenario configurations, packet hashes and event chains. A changed build cannot silently
resume an old protocol: retain its archive/evidence and freeze a new protocol.

| Method | Route under /api/contract-research | Purpose |
|---|---|---|
| GET | /catalog | Research defaults, scenarios and packet limit |
| POST | /example | Explicitly import the fictional demonstration |
| GET, POST | /protocols | List or freeze immutable protocols |
| GET | /protocols/{id} | Inspect binding, attempts, packets and audit |
| POST | /trials | Reserve and dispatch one budgeted attempt |
| POST | /trials/{id}/recover | Recover the exact dispatch |
| POST | /trials/{id}/control | Pause, resume or cancel |
| POST | /packets | Capture a terminal inventory |
| GET | /packets/{id} | Inspect a summary, including historical evidence |
| GET | /packets/{id}/export | Download summary or permitted private traces |
| POST | /verify | Data-only integrity/identity verification |

See [freeze schema](freeze.schema.json), [trial schema](trial.schema.json),
[capture schema](capture.schema.json) and the running /api/docs.
Mutations retain the existing local same-origin/header boundary and redacted validation.
Authenticated team roles, separation of author/reviewer and organization isolation are MVP-3.

## Validation and remaining acceptance

[Acceptance cases](acceptance.md) map the delivered behavior to focused and browser checks.
[Workspace validation](../workspace-validation.json) includes all existing suites plus the
new [controlled research journey](../contract-research-browser-validation.json).
[Deployment](../workspace-deployment.json) and [deployed UI checks](../workspace-deployed-browser.json)
record the actual running state; they are not default authority settings.

No customer interviews, paid pilots, commercial feed acceptance, genuine broker executions
or proof of statistical edge were created by this implementation. Single-session simulated
cost stress is a diagnostic. Independent financial acceptance remains MVP-4 work.
