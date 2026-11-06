# MVP-1 acceptance inventory

Engineering acceptance is separate from external customer/data acceptance.
The implementation record links actual final validation results.

| ID | Acceptance boundary | Evidence |
|---|---|---|
| AC-1 | Exact typed, bounded strategy/data/customer contract | Schemas and strict-input tests in ../../tests/test_contracts.py |
| AC-2 | Call/put vertical and condor structure, shared expiry and same-session rules | Shape, expiry, ID, timing and unsupported-rule tests |
| AC-3 | Integer-paise payoff, complete-leg budget and dated terms | Independent call-spread and unequal-width-condor arithmetic; budget/tick/cutoff tests |
| AC-4 | Actual immutable dataset compatibility, no fabricated liquidity | Frequency, lot changes, boundary coverage, source and Parquet integrity tests |
| AC-5 | Customer/source unknowns remain explicit | Provisional/review-reference tests; synthetic and commercial-rights findings |
| AC-6 | Immutable revisions, idempotent request binding and recovery | Parent, restart, tamper, rollback, request rebinding and backup tests |
| AC-7 | New sidebar workspace preserves existing options | ../../tools/contracts_browser.py plus the existing ten browser suites |
| AC-8 | Safe requests, imports and display | Same-origin, oversize, redacted-error, unsupported JSON and escaped-name cases |

## Remaining acceptance dependencies

- Real customer artifacts, interview references and measured review baseline.
- A source agreement and genuine option-history coverage acceptance.
- An independently reviewed first-receipt/availability adapter.
- Later-stage execution adapter and genuine prospective observations.

The first three are surfaced in the workspace. Missing dependencies must stay visible;
controlled fixtures cannot establish that they have been satisfied.

## How to reproduce

Focused tests: .venv/bin/python -B -m pytest tests/test_contracts.py -q
Controlled UI: .venv/bin/python -B tools/contracts_browser.py
Complete regression: .venv/bin/python -B tools/validate_workbench.py

All test/browser account mutations are confined to temporary stores. Screenshots contain
fictional data and are labeled accordingly. Existing warnings and actual limitations
must remain in the final validation record.
