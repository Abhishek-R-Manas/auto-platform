# Customer workflow and evidence intake

Status on 2026-11-06: target workflow is a hypothesis; no customer interviews or budgets
have been validated through this implementation.

Target: professional options research, RA/algo-provider or options desk teams that repeatedly
evaluate strategies and have an accountable reviewer. The job is to decide what deserves
further testing, retain the reasons and compare later behavior.

## Intake to complete with a real customer

| Question | Evidence to record |
|---|---|
| Who proposes and reviews a strategy? | Actual roles and authority; names only where necessary |
| Which strategy types are repeatedly reviewed? | Redacted real specification or review artifact |
| How are entry, exit, strike choice, size and revisions represented? | Existing tools and an example with exact rules |
| Which decisions are costly or error-prone? | Documented rework, rejected assumption or manual check |
| How long does a review take today? | Measured baseline and measurement method |
| Who owns budget and procurement? | Actual budget owner and constraints; no inferred willingness to pay |
| Which datasets may be used in this deployment? | Coverage, agreement references and permitted use |
| What is a successful pilot? | Agreed review-time, correction-quality and repeat-use measurements |

Keep provisional assumptions labeled PROVISIONAL. Mark REVIEWED only with an actual
evidence reference and budget owner; the application still labels this an owner record,
not independent identity or licensing verification.

Store redacted references or locally appropriate records. Do not put credentials, private
customer strategies, portfolios or personal data into public fixtures or screenshots.

## Initial workflow

Author exact supported definition -> inspect rule interpretation and missing evidence ->
retain a revision -> hand the frozen definition to MVP-2 evaluation -> reviewer inspects
actual results -> prospective comparison after the later stages are implemented.

The application currently implements definition/intake and immutable revisions. It does
not send invitations, contact customers, issue invoices or claim paid pilot outcomes.
