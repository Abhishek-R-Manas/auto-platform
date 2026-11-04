# Phase 3: AI candidate research and strategy registry

Implementation date: 2026-11-04. Mode: **research and paper review only**.
Planning baseline: [Phase 3 baseline](phase-3-plan.md).

## Release scope and quality standard

Strategy lab is available at `http://127.0.0.1:8787/#candidates` through the existing SSH tunnel.
It implements study protocols, AI/manual authoring, immutable candidate versions, reproducible
screening, holdout freeze, independent review, rejection/retirement, audit and evidence export.
These are software capabilities. They do not establish a trading edge or an independently
benchmarked industry-leading platform. Professional quality requires measured operating
experience, independent quantitative/accounting/security review and actual supplier validation.

The application currently has no dedicated AI credentials. The model connector is implemented
and tested with controlled responses; no genuine provider generation has been validated.
Kite price authentication also remains separate and pending. The NSE instrument master is real
metadata, not price history. Dataset/model owner acceptance from Phase 2 remains open.

## Operator workflow

1. In Research lab, import and inspect suitable immutable data. Explicitly labelled synthetic
   fixtures support software checks and always block paper qualification.
2. Open Strategy lab. Select the dataset and create a study, declaring training/validation
   proportions, embargo, capital, risk/execution assumptions, research thresholds and budgets.
3. In Candidate builder, enter a trading idea and explicitly request a generation from the
   configured model. Alternatively, use an unvalidated reference specification or author a
   strategy JSON object. Manual output is labelled MANUAL; model output is labelled AI with
   actual response provenance. An unconfigured model never silently generates a template.
4. Inspect the immutable registry version. Revise by creating a child version; existing
   specifications, hypotheses and provenance are not edited in place. Unsupported capabilities
   remain blocked. Invalid model proposals and failed requests remain in the generation journal.
5. Run the screening pipeline. Inspect each scenario, accounting checks, actual sample/fill
   evidence, thresholds, assumptions and source reports. Pause, resume or cancel when needed.
6. Compare candidates within the declared protocol. Select the exact finalist and freeze the
   study before evaluating holdout data. This consumes the holdout and blocks further candidate
   creation, revisions and model feedback in that data lineage.
7. Record independent source/rights, calendar/actions, charges/margin/settlement and execution
   review, with reviewer and evidence notes. This records an operator assertion, not supplier
   verification performed by software.
8. Approve a paper review packet only if all data, screening and holdout gates pass. Otherwise
   reject or retire, preserving evidence. Export the study/version lineage and review packet.
   A packet does not activate a strategy; Phase 4 owns forward evaluation and monitoring.

## Private model setup

Add the following documented variables to the private server environment or `.env.local`
(mode 600, outside the web root), then restart the local launcher:

- `ARTHA_AI_API_KEY`: an API key dedicated to this application.
- `ARTHA_AI_MODEL`: an available Responses model supporting structured JSON output.

No model is assumed to be available on the owner's account. Model selection and provider
billing belong to that account. The application never reads Codex/agent authentication files,
never consumes a coding-agent API key and never puts the key in prompts, exports or browser
responses. Configure it privately, not through chat or the strategy editor.

A generation is an explicit action against the configured account and can incur provider
usage. The status distinguishes configured from actual successful generation. Model id,
response id, usage, prompt version/hash, developer instructions, schema hash, input context
and visible output are retained. Internal model reasoning is not collected. `store=false`
is requested; this does not independently establish provider-wide zero-retention guarantees.

The endpoint is fixed to `https://api.openai.com/v1/responses`, with verified TLS 1.3,
redirects and environment proxies disabled. Requests have no tools. Model output is a strict
structured envelope containing a strategy JSON string, hypothesis, risks and assumptions;
that string is independently validated against the deterministic engine's Strategy contract.
There is no Python execution, shell, filesystem tool, broker order tool or URL supplied by
model output. The provider interface permits controlled tests without fabricated real AI.

## Immutable study and information boundaries

Study configuration binds dataset id, chronological windows, selected training instruments,
capital, risk, execution, research thresholds and budgets. The protocol hash records these
inputs. At least 40 training observations and usable validation/holdout windows are required;
bar counts alone do not establish sufficient independent financial samples. Default screening
also requires at least 20 evaluation days and one fill record. These are editable research
filters, not recommended investment limits or proof of statistical significance.

One protocol is allowed per declared dataset lineage. A normalized root-data hash also catches
renaming an identical dataset to reset the protocol. Revisions remain in the same lineage.
This prevents ordinary workflow retries and repeated finalist selection from silently
reopening a holdout. The owner can still inspect/export source data through Research lab;
this is an experimental protocol guard, not secrecy against the data owner. Partially
overlapping datasets and falsely declared source provenance still require independent review.

AI input includes only instruments observed during training, training-time contract terms and
bounded trailing training observations. Selected references are limited to 50 per study; larger
universes require explicit data/study partitioning. Parent revisions include their specification
and proposed rationale, without validation or holdout performance. No holdout prices are sent
by this workflow. A pretrained LLM can nevertheless know later historical events. Results
require eventual prospective paper evidence; historical isolation cannot remove model training
contamination or guarantee a strategy's future value.

## Registry and states

Candidate identities are content hashes over study, specification, hypothesis, risks,
assumptions, parent, origin, model provenance and implementation build. Hash verification runs
on reads. Strategy ids and versions are recorded in underlying replay evidence.

Typical stages:

`VALIDATED → EVALUATING → SCREENED → HOLDOUT_RUNNING → HOLDOUT_REVIEW → PAPER_APPROVED`

`BLOCKED`, `REJECTED` and `RETIRED` preserve unsuccessful work. Rejecting/retiring requests
cancellation of outstanding candidate evaluations. Approval records reviewer, note, source
review, dataset hashes, model provenance and evaluation hashes in an immutable packet. Old
packets remain historical evidence; future activation must check current registry state.

Generation states include QUEUED, RUNNING, SUCCEEDED, FAILED, CANCEL_REQUESTED, CANCELLED and
INTERRUPTED. Invalid/refused/incomplete model responses retain visible output and known usage
when available. Transport failures retain a safe error; HTTP credential-bearing diagnostics
and response headers are never persisted. Uncertain requests are never retried automatically.

## Evaluation and acceptance

One screening evaluation reserves eight research runs and preserves capacity for the final holdout:

1. Training replay.
2. Identical training replay to compare economic hashes.
3. Chronological validation.
4. Cost stress: increased spread/slippage, reduced participation and additional latency.
5. First validation walk-forward segment.
6. Second segment with the predeclared expanding fit cutoff and embargo.
7. Stop distance at 80% of the candidate specification.
8. Stop distance at 120%.

The learned ridge family uses an earlier fit cutoff for its training replay. Ordinary
validation and holdout keep their declared training cutoff; no adaptive holdout fitting is
performed. Each job uses isolated accounting and the existing deterministic research engine.
No evaluation writes the main paper account.

Reports check ledger/attribution reconciliation, obligations, policy halts, reproducibility,
scenario fill/drawdown/return filters, main evaluation sample and final quote freshness.
Every job's configuration, code hash, dataset hash, seed, report and economic hash remain
available. Threshold failures are visible even if the job completed technically.

Search diagnostics retain version/generation attempts and include stop-distance variants in
comparable validation trials. Estimated Deflated Sharpe and block bootstrap are diagnostics;
correlated trials, sample limitations, data defects and LLM contamination remain material.
No AI-generated narrative can override checks or substitute invented evidence.

Finalist selection atomically reserves one holdout attempt and freezes the study. Cancellation,
worker failure or an uncertain interruption does not grant a fresh holdout. A compatible paused
attempt can resume its existing checkpoints. Another candidate cannot replace the finalist.
Optimistic favorable-first fills remain sensitivity evidence and cannot qualify a candidate.
Synthetic data, missing usage rights, unconfirmed point-in-time universe/actions, incomplete
operator review, failed screening/holdout gates or a non-finalist all block paper approval.

## Budgets, persistence and recovery

Defaults: eight candidate versions, eight generation requests, 80 replay runs, 6,000 output
tokens per request and 24,000 reserved output tokens per study. Explicit maximum-output
reservations are conservative and retained after uncertain/failed requests. Thus a default
study can exhaust its token reservation before its request count. Input context is bounded
by selected instruments/observations and a 120,000-character training-context limit; actual
input/output token counts come from the provider. No unverified currency-cost estimate is
presented. Model/context/schema overhead also contributes to provider input usage.

A single model worker and at most eight active/queued evaluations bound concurrency. Research
workers retain the Phase 2 replay-time and queue limits. Candidate evaluation controls operate
between replay steps. Cancelling a running model request suppresses candidate creation but
cannot guarantee the provider stopped processing or charging it.

State lives in `data/strategy-lab/registry.sqlite3`, separate from research and paper accounting.
Nested SQLite savepoints preserve state, budgets and audit together. Dispatch recovers an
already-created replay only when its configuration and engine-build hashes match after an interruption. Replay/model worker
credentials are separated; replay workers strip broker and AI keys.

On restart, uncertain model jobs become INTERRUPTED and unfinished evaluations pause. Resume
requires the original code build. Source/environment identities remain bound to every
attempt; incompatible runs are not quietly resumed under new code. Back up this SQLite file
with its WAL using the existing online backup tool. A restart pauses new main-account entries.

Audit events form a local SHA-256 chain. Verification failures disable registry writes and
mark health degraded. This detects ordinary modification; it has no external trust anchor,
cryptographic signing, immutable storage guarantee or protection against complete privileged
rewriting. Production multi-user roles, independently anchored audit, managed secrets,
encrypted backup operations and tested failover remain further platform-hardening work.

## API and evidence

Routes under `/api/candidates` cover `status`, `studies`, study `references/data-review`,
`versions`, version `evidence/evaluate/holdout/review/export`, `generations`, generation
`cancel`, `evaluations`, evaluation `control`, and `audit`. Strict request schemas, local host,
same-origin mutation headers and request-size limits apply. Generation/evaluation request ids
bind inputs for idempotent retries; reusing an id for different inputs fails.

Exports contain the selected candidate, all study lineage versions, study protocol, generation
records, scenario results, source/data hashes, matching review packets and relevant audit
events. Individual replay evidence remains available through the original Research lab APIs.
Never delete failed candidates or trials merely to improve reported selection statistics.

Implementation: `app/candidates/`, `static/candidates.js`, `tests/test_candidates.py` and
`tools/candidates_browser.py`. Test artifacts explicitly identify controlled models, synthetic
data and simulated supplier assertions. They are not genuine provider or market validation.

CodeGuard checks cover server-only credentials, approved hashes, certificate verification,
fixed egress, schema/input validation, same-origin API boundaries, safe serialization, storage,
audit, cancellation and worker isolation. No embedded certificates or credentials are added.
The existing broker TLS compatibility exception remains documented in [live-data.md](live-data.md).

## References checked on 2026-11-04

- [OpenAI structured output contract](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
- [OpenAI Responses and text generation](https://developers.openai.com/api/docs/guides/text).
- [Phase 2 research integrity and modeling limits](phase-2.md).
- [Product roadmap and phase boundaries](product-plan.md).

Final release/deployment evidence is recorded after actual checks, alongside this guide.


## Deployed release evidence

The Phase 3 service is deployed at `http://127.0.0.1:8787/#candidates` behind the existing
SSH tunnel. **145 automated tests** and **52 controlled browser workflows** passed: 14 paper,
13 historical research, 10 market data and 15 Strategy lab workflows. All four JavaScript
syntax checks, dependency consistency and documentation-link checks passed. One known
Starlette TestClient/httpx deprecation warning is recorded.

The deployed registry contains an explicitly labelled **authored synthetic software example**.
Its eight screening scenarios and holdout completed, repeat economic hashes matched, and
main-account cash/orders were unchanged. It was intentionally REJECTED for qualification;
synthetic outputs never become qualified trading evidence. A separate read-only browser check
verified the actual deployed UI, retained results, blocked holdout reuse, unconfigured-model
controls, mobile layout and audit integrity without JavaScript errors.

Final recovery checks exclude cached replays from other engine builds and preserve a replay
slot for final holdout evaluation. Application source and pinned dependency locks are archived
under `data/releases/{code_hash}/`; both the demonstration's original source and the final
recovery build were verified against their recorded hashes. Completed evidence retains its
original build across maintenance restarts. Archives contain no account databases or credentials.

- [Release validation](phase-3-validation.json).
- [Controlled Strategy lab browser evidence](phase-3-browser-validation.json).
- [Actual deployed browser evidence](phase-3-deployed-browser.json).
- [Deployment, backup and source-archive evidence](phase-3-deployment.json).
- [Deployed example screenshot](screenshots/phase3-deployed-evidence.png).

The AI model is **not configured**, so no genuine model request is claimed. Configure the
application's dedicated key and model privately to enable generation. Real-price authentication,
real historical/economic acceptance, independent quantitative/security review, prospective
paper evidence and competitive benchmarking remain open. These release checks establish
software behavior, not an industry-leading certification or trading advantage. Real-money and
unattended forward execution remain disabled.
