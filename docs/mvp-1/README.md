# MVP-1: Strategy Contracts

Implemented 2026-11-06. This is the first stage of the
[five-stage B2B MVP roadmap](../mvp-validation-roadmap.md), separate from engineering Phase 1
and the broker-execution Phase 5.

This document records the MVP-1 delivery boundary. [MVP-2](../mvp-2/README.md) now
adds a separate **Research & evidence** tab with the execution adapter and recorded-clock
handling. The original definition assessment still keeps source authenticity/rights
unverified. MVP-1's dated implementation record remains unchanged.

## Delivered scope

Open **Strategy Contracts** in the main sidebar, or visit /#contracts. Existing Home,
Trade, Strategy Builder, My Strategies, Portfolio, research and pilot workflows remain
available. No existing strategy-to-backtest behavior was replaced.

This workspace defines one scheduled, fixed-contract, same-session NIFTY call vertical,
put vertical or iron condor. It records the customer research question, dated contract
terms, exact leg identities, scheduled entry/exit, units, loss budget and data requirements.

The interpretation is explicit: no overnight holding, reentry, dynamic strike selection,
adjustments or expiry rolling. Hedges-first/unwind-on-failure is a declared requirement for
the next-stage adapter, not an implemented execution guarantee.

## Use the feature

1. Choose one of the three fictional examples, or import a definition JSON.
2. Review the research question and customer context. A reviewed customer record requires
   a budget owner and actual evidence reference; both remain owner assertions.
3. Enter stable historical instrument IDs, dated lot/tick terms, scheduled IST times and
   the exact option legs.
4. Set the research expiry-loss budget and estimated costs. Monetary values are displayed
   in rupees and stored/calculated as integer paise.
5. Select an existing immutable Research lab dataset when available. Declare the required
   fidelity, quote age, displayed depth, spread and source/use scope.
6. Select **Check definition**. Definition, data and customer evidence have separate outcomes.
   Editing invalidates the displayed assessment; the server reassesses every save.
7. Select **Save revision**. Business-incomplete definitions can be retained for further work.
   Reopening and editing a saved revision produces a parent-linked immutable record.
8. Export the current definition or a saved revision. Importing an exported saved record
   extracts its definition and reassesses it; its old assessment is not trusted as current.

## Contracts and API

- [Strategy schema](contract.schema.json).
- [Data requirements schema](data-requirements.schema.json).
- [Call vertical example](examples/call-vertical.json).
- [Put vertical example](examples/put-vertical.json).
- [Iron condor example](examples/iron-condor.json).
- [Over-budget example](examples/over-budget.json): structurally valid, definition blocked.
- [Unsupported roll example](examples/unsupported-roll.json): rejected by the schema.
- [Data contract and acceptance dependencies](data-contract.md).
- [Customer workflow intake](customer-workflow.md).
- [Acceptance inventory](acceptance.md).

Endpoints are documented in the application's /api/docs:

| Method | Route | Behavior |
|---|---|---|
| GET | /api/contracts/catalog | Supported structures, rules and fictional examples |
| GET | /api/contracts/schema | Current definition JSON Schema |
| POST | /api/contracts/check | Validate and assess a definition plus optional dataset ID |
| GET | /api/contracts | Up to 200 most recent revision summaries |
| POST | /api/contracts | Save with a unique request_id, definition, optional dataset_id and parent_id |
| GET | /api/contracts/{id} | Read a hash-verified immutable revision |
| GET | /api/contracts/{id}/export | Download the saved revision JSON |

Check requests use an envelope with definition and optional dataset_id. Schema files
describe the definition itself. Mutations require the existing same-origin boundary and
X-Artha-Client: paper-workspace header. The application remains a local-user workspace;
authenticated commercial team roles are MVP-3 work.

Unknown fields, executable-code fields, unsupported rules, ambiguous timestamps and
non-integer money/quantity inputs are rejected. API validation responses omit submitted
values. Requests retain the existing 16 KB server limit; client JSON files are bounded
at 64 KB before extracting the definition.

## Interpretation and financial limits

Structure checks require distinct option IDs, common expiry, equal leg units, appropriate
wings and matching option types. Entry/exit ordering, one local session, effective terms,
premium ticks and research loss budget are checked separately.

The loss/profit preview evaluates the contractual common-expiry payoff using declared
premiums, complete legs and estimated costs. It is not a prediction, live quote, margin
calculation or guarantee about partial fills, early exits, gaps or settlement.

Dataset checks read the actual immutable catalog and verify its raw/Parquet integrity.
They compare identities, strikes, expiry, underlying, effective lot/tick terms, session,
frequency and dated fee availability. Quote checks inspect observations at or before both
scheduled boundaries, using declared age, spread and minimum displayed depth. They do not
establish continuous-window coverage, exchange queue position or actual fills.

Current research rows do not independently establish first-receipt timestamps.
RECEIPT_TIMES therefore remains UNVERIFIED and data_ready remains false in this release.
Owner receipt-time notes, licensing references and checkboxes do not override that limit.
MVP-2 needs an appropriate timestamp/provenance adapter before this evidence can pass.

commercial_data_ready and rights_independently_verified remain false. Customer-reviewed
records remain assertions with references, not verified professional credentials.
Synthetic data is explicitly identified and cannot establish genuine market evidence.

## Storage and recovery

Definitions use new mvp_contracts and mvp_contract_requests tables inside the existing
workspace SQLite store. Existing records are preserved. Saves use the shared workspace lock
and a database transaction. Content hashes cover the definition, dataset binding,
parent and assessment. Request IDs bind exact inputs; retries cannot point to another
definition. Failed saves roll back without partial request rows.

Read and backup checks verify record hashes and request bindings. The existing all-store
backup/restore machinery covers the new tables; no new database path is required.
Local hashes provide integrity checks, not an external authenticity anchor.

## Validation and deployment evidence

Run tests/test_contracts.py for focused contract, payoff, data, security and storage cases.
Run tools/contracts_browser.py for the controlled desktop/mobile journey. It uses a
temporary account/database, fictional inputs and no genuine provider or broker calls.

The complete tools/validate_workbench.py validator now includes this browser suite and the
ten existing suites. Actual results belong in [workspace validation](../workspace-validation.json)
and [contract browser validation](../contracts-browser-validation.json).
Deployment state belongs in [workspace deployment](../workspace-deployment.json), not in a
feature's default settings. See the [dated implementation record](implementation.json)
for the final evidence handoff.

## Remaining external work

No customer interview, paid pilot, commercial feed agreement or genuine options-data
acceptance was manufactured. Complete the intake and source acceptance with actual evidence.
The new feature sends no orders, creates no backtest jobs and starts no forward portfolios.
MVP-2 packages reproducible evaluation; MVP-3 adds commercial team identities and approval.
