# B2B strategy-validation MVP: five-stage roadmap and handoff

Recorded: 2026-11-06 (Asia/Kolkata).
Updated: 2026-11-07.
Status: MVP-1/MVP-2/MVP-3 engineering implemented and deployed. MVP-4 software validation is implemented and deployed; independent financial review and genuine-data calibration remain pending. Real off-host destination/recipient and customer/data/public-host acceptance also remain pending.
Current handoff: finish external financial/source/deployment acceptance and operate the MVP-5 workflow on actual customer work. MVP-5 operating software is implemented and deployed; customer adoption, payment/renewal verification and genuine elapsed operation remain pending. Check the stage implementation records for actual release/deployment evidence.

## Purpose and conversation context

The owner asked for a thorough assessment of Auto Platform/Artha, whether it has a
differentiated MVP that could attract VC interest, and which features and implementation
plan would improve that prospect. A follow-up asked whether the proposal is technically
achievable. The owner then requested that the five delivery stages be stored in this
repository so other models can resume with the same context.

The recommendation is a paid B2B strategy-validation, approval and prospective-monitoring
workspace for professional Indian options research teams. This is a commercial hypothesis
to validate, not demonstrated product-market fit, global uniqueness, trading alpha,
regulatory clearance or a funding commitment.

The owner's broader Centaur Strategy Forge direction remains recorded in
[commercial strategy](commercial-strategy.md). Retain stocks, futures and broader options
capabilities. A narrow first customer journey does not remove existing functionality or
replace that direction.

These stages are named MVP-1 through MVP-5. They are separate from the existing six
engineering phases in [the product roadmap](product-plan.md). In particular, MVP-5 means
early-customer operation; engineering Phase 5 concerns the separately gated broker pilot.
Recording this plan does not implement a stage, change deployment or expand order authority.

## Where we are now

| Work item | Status on 2026-11-07 | Evidence / next action |
|---|---|---|
| Repository and workflow analysis | Complete for this planning discussion | Inspected research, candidates, forward, workbench, pilot, API, operations, docs and recorded validation |
| Competitive and commercial assessment | Complete as a dated assessment | See findings and source links below; refresh changing claims before relying on them |
| Technical feasibility assessment | Complete as a planning judgment | Scoped MVP is achievable; data quality and financial correctness are the hardest dependencies |
| MVP-1 | ENGINEERING IMPLEMENTED; external acceptance pending | [Delivered guide, schemas, examples and actual validation](mvp-1/README.md); customer interviews, source rights and receipt-time acceptance remain pending |
| MVP-2 | ENGINEERING IMPLEMENTED AND DEPLOYED; external acceptance pending | [Frozen research, evidence exports, API schemas and validation](mvp-2/README.md); commercial data/customer acceptance remains pending |
| MVP-3 | ENGINEERING IMPLEMENTED AND DEPLOYED; external off-host/customer acceptance pending | [Authenticated team workflow, schemas, recovery and actual evidence](mvp-3/README.md); real SSH target/recipient and public team-host acceptance remain pending |
| MVP-4 | ENGINEERING IMPLEMENTED AND DEPLOYED; independent financial/data acceptance pending | [Golden financial cases, read-only results, validation and acceptance handoff](mvp-4/README.md) |
| MVP-5 | ENGINEERING IMPLEMENTED AND DEPLOYED; actual customer operation/acceptance pending | [Customer pilot workflow, reports, cohort measurement and actual evidence](mvp-5/README.md) |

The initial planning discussion performed no MVP implementation or customer validation.
The subsequent owner-authorized MVP-1 and MVP-2 implementations are now recorded below; see [MVP-2](mvp-2/README.md) and
[mvp-1/implementation.json](mvp-1/implementation.json). Customer interviews, paid pilots,
data purchases and new broker actions were not performed.

Owner instructions for this workstream: preserve existing workflows, add the new feature
as a separate sidebar option, and proceed without skills. The feature is **Strategy Contracts**.

### Recorded software baseline

- Workspace version: 0.20.0.
- [Recorded validation](workspace-validation.json), checked 2026-11-05, reports 518 passing
  automated tests. The planning review recomputed the source/asset fingerprint and matched
  all 188 recorded files. The suite was not rerun during that review.
- Matching fingerprint:
  d06bb0da4344c7c9636a557154c672938bae53f18a734d95028515c227da25a8.
- Existing capabilities include the 51-template builder, immutable studies and candidate
  versions, bounded declarative AI proposals, historical replay, holdout workflow controls,
  prospective paper books, deterministic risk, incidents and evidence-bound owner reviews.
- Controlled tests establish the behavior tested, not genuine provider acceptance, a
  trading edge, commercial data rights or multi-user production readiness.
- The latest [recorded deployment](workspace-deployment.json) says manual broker transport
  was enabled, the account was unarmed, order count was zero and no strategy was qualified.
  Older summaries describe transport as off. This is a dated record, not a fresh runtime
  assertion. Check actual deployment and the [manual trading guide](manual-trading.md)
  before any execution-related work. Keep manual authority separate from strategy approval.

## Product hypothesis and initial scope

Target customer: a small systematic options research team, RA/algo-provider business or
desk that repeatedly evaluates strategies, has an accountable reviewer and a budget owner.

Customer job: decide which strategies deserve further testing or approval, retain the
evidence behind that decision, and observe subsequent behavior.

The intended journey is:

Describe/import a supported strategy -> freeze its protocol -> reproduce evaluation ->
review exceptions -> approve an explicit scope -> monitor prospective paper behavior.

Initial implementation scope:

- NIFTY options, same-expiry defined-risk spreads, with a small explicit rule set.
- One suitable data source with established coverage and permitted use.
- One organization per isolated deployment initially, with authenticated team members.
- Explicit entry, exit, sizing, strike/expiry selection, session and risk behavior.
- Historical evaluation, review packets and prospective paper monitoring.
- Retained revisions, unsuccessful experiments, limitations and uncertainty.
- Existing broader platform features preserved.

Deferred: arbitrary Python/Pine/framework execution, universal external-strategy
certification, a marketplace, broad broker/asset expansion, autonomous derivatives
execution and an Artha-employed retail guardian service.

## Feasibility and scheduling assumptions

No unresolved scientific breakthrough is needed for the scoped workflow. Existing engines
provide a useful starting point, but implementation completeness cannot be estimated from
feature count alone.

Twelve weeks is a plausible narrow-pilot target. Plan for 12-16 calendar weeks with three
engineers covering backend, frontend and data/infrastructure, plus access to an independent
quant reviewer and founder-led discovery. These are planning estimates, not commitments.
Data contracting, customer procurement and elapsed prospective evidence have independent
timelines.

Stages may overlap. Develop the independent validation cases early and start prospective
observation once the strategy/data contracts and necessary access controls are stable.
Unavailable commercial data need not halt fixture-based development; keep fixture evidence
distinct from genuine-data validation.

## MVP-1 — Define supported strategies and the data contract

Status: ENGINEERING IMPLEMENTED AND DEPLOYED; external customer/data acceptance pending.
Delivered artifacts and measured evidence: [MVP-1 guide](mvp-1/README.md) and
[implementation record](mvp-1/implementation.json).

The app now captures the declared workflow, checks supported contracts and datasets,
retains immutable revisions and surfaces missing evidence. Actual customer review and
commercial source acceptance have not been fabricated.
Estimate: approximately 2 weeks.
Suggested owners: product/founder, backend and quant/data reviewer.

Deliverables:

1. Define the first customer workflow and budget owner using actual review artifacts.
2. Select the exact supported strategy structures and rule vocabulary.
3. Specify entry/exit timing, sizing, dynamic contract selection if supported, leg ordering,
   adjustments, expiry behavior and missing-data behavior. Reject unsupported instructions.
4. Define the historical/prospective data contract: stable contract identities, dated
   lot/tick specifications, timestamps, bid/ask/depth where required, fees, source coverage,
   publication/receipt cutoffs and usage rights.
5. Define an intent-to-rule mapping so the described, replayed and monitored strategy has
   the same behavior.
6. Record scope exclusions, uncertainty and an initial acceptance-case inventory.

Concrete code gap: [the basket backtest bridge](../app/workbench/service.py) maps legs to
specific historical instruments and constructs a basic RULES entry condition equivalent
to underlying close > 0 for option baskets. This supports a fixed-basket experiment;
it is not an implementation of arbitrary recurring entries, adjustments or expiry rolls.
Extend the contract deliberately before claiming those behaviors are tested.

Exit criteria:

- Every supported rule has an unambiguous interpretation and required inputs.
- Example strategies can be mapped into the declared contract without silently invented rules.
- Missing data or unsupported instructions produce visible blockers.
- Data availability and commercial-use dependencies are documented, even if contracting
  remains in progress. Customer release depends on the required rights being established.

## MVP-2 — Package the research and evidence workflow

Status: ENGINEERING IMPLEMENTED AND DEPLOYED; actual final validation/deployment in [MVP-2 implementation record](mvp-2/implementation.json).
Original planning estimate: approximately 3-4 weeks.
Suggested owners: backend, frontend and data engineer.
Dependencies: MVP-1 strategy/data contract; coordinate identity requirements with MVP-3.

Reuse [research](../app/research/), [candidates](../app/candidates/) and
[workbench](../app/workbench/) rather than introducing a second financial engine.

Deliverables:

1. One guided workflow from supported specification to immutable protocol and evaluation.
2. Bind organization, strategy version, protocol, dataset snapshot, engine build, risk policy,
   observation cutoff and event head into each evidence packet.
3. Preserve all trials, failures, revisions, costs and declared execution assumptions.
4. Show separate findings for data quality, reproducibility, execution realism, research
   robustness and operational readiness. Unknown evidence stays unknown.
5. Export inspectable packets with a verification path and permission-appropriate artifacts.
6. Initially accept the supported Artha format. An external result without reproducible
   inputs remains labeled unverified; add an adapter only for demonstrated customer demand.

Exit criteria:

- Identical pinned inputs reproduce the economic results.
- Changed inputs/builds have distinct identities; stale or mismatched evidence is rejected.
- Incomplete evidence is flagged without fabricating prices, execution or performance.
- A user can inspect how the tested rules match the original specification.
- Historical and prospective information boundaries are exercised by meaningful checks.

## MVP-3 — Add team identity, approval and recovery

Status: ENGINEERING IMPLEMENTED AND DEPLOYED; actual validation/deployment in [MVP-3 implementation record](mvp-3/implementation.json). Real off-host target/recipient and public customer-host acceptance remain pending.
Original planning estimate: approximately 2-3 weeks, partly parallel with MVP-2.
Suggested owners: backend and platform engineer.

The existing [API](../app/api.py) is a local-user application. The
[current forward review](../app/forward/service.py) records LOCAL_OWNER_ASSERTION.
Those local-owner paths retain that trust boundary. The new [team-only application](mvp-3/README.md) provides separately authenticated organization stores and roles; it does not expose the owner or broker APIs.

Deliverables:

1. Authenticated identities and server-enforced researcher/reviewer/administrator permissions.
2. Organization isolation; evaluate separate deployments/stores for the first customers.
3. Review comments, author/reviewer separation where required, approval scope, expiry and
   revocation bound to exact strategy/protocol versions.
4. Distinguish review of an immutable evidence snapshot from approval of a bounded operating
   scope. Define material changes requiring renewed review so every quote does not demand it.
5. Off-host backups, retention and demonstrated isolated restore, with secrets protected.
6. Keep optional AI explanation separate from deterministic research validity. The existing
   genuine-AI-monitor qualification gate needs explicit design review for this workflow.

Exit criteria:

- Unauthorized reads, mutations, approvals and cross-organization access fail.
- Reviewer identity comes from authenticated server context, not a browser-entered name.
- Expired/revoked/mismatched approvals cannot authorize new operation.
- Restore and paused-restart behavior preserve evidence and existing account boundaries.
- No existing live authority or risk gate is weakened by the new review model.

## MVP-4 — Validate with independent financial cases

Status: ENGINEERING IMPLEMENTED AND DEPLOYED; independent reviewer and genuine-data acceptance remain pending.
The [MVP-4 guide](mvp-4/README.md) describes golden cases, read-only owner/team views, build-bound evidence, regression gates and the independent-review handoff. Actual outcomes belong in [implementation.json](mvp-4/implementation.json). Software checks do not satisfy the external acceptance exit criteria below.
Estimate: approximately 3-4 weeks, partly parallel with MVP-2 and MVP-3.
Suggested owners: independent quant reviewer, data engineer and backend engineer.

Deliverables:

1. Independently labeled valid and faulty cases, including payoff/accounting, dated contract
   terms and charges, look-ahead inputs, exposed evaluation data and reproducibility.
2. Execution cases for spreads, depth, latency, missing quotes, partial/rejected legs,
   unavailable exits and expiry boundaries, according to the supported scope.
3. Cross-check numerical outputs and calibrate assumptions against suitable genuine data.
4. Measure false alerts and missed issues against reviewed controls; retain sample sizes
   and limitations rather than claiming universal detection.
5. Exercise genuine configured AI responses if offered, while retaining deterministic
   checks and refusing unsupported/generated executable code.
6. Record actual check outcomes, source fingerprints and genuine-versus-controlled evidence.

Exit criteria:

- Independent reviewers agree on expected case behavior within declared numerical tolerances.
- Known material defects are detected and reviewed clean controls behave correctly.
- Genuine-data calibration limits are disclosed; insufficient evidence produces a blocker.
- Reproducibility, accounting, information cutoffs, permissions and recovery checks pass.
- Operational readiness is not relabeled statistical significance or future profitability.

## MVP-5 — Operate with early customers

Status: ENGINEERING IMPLEMENTED AND DEPLOYED; actual repeated customer operation, payments/renewals and four-week evidence remain pending.
The [MVP-5 guide](mvp-5/README.md) records exact-cycle binding, independent-review timing, prospective observation journals, discrepancy review, effort and administrator-only commercial records, plus distinct-organization cohort reporting. This journal references a separately operated paper book and grants no execution authority. Actual validation/deployment belong in [implementation.json](mvp-5/implementation.json). The external exit criteria below are not satisfied by implementation or controlled test clocks.
Estimate: 4+ weeks, overlapping development once customer-facing dependencies are ready.
Suggested owners: product/founder, customer team and engineering.

Deliverables:

1. Run repeated customer specification -> evaluation -> review -> prospective-paper journeys.
2. Observe frozen strategies and explain data gaps, execution differences and material changes.
3. Measure baseline versus Artha review time, reviewer/support effort and documented corrections.
4. Record actual invoices/payments, repeated usage, onboarding effort, renewals and delivery costs.
5. Add the most valuable missing integration only when repeated customer evidence justifies it.

Proposed commercial experiments, not observed outcomes:

- Test offers around INR 25,000/month, INR 50,000/month and INR 100,000+/month for different
  scopes/deployment needs. Account for actual data, model, compute, review and support costs.
- Aim for five paying organizations, at least three first-term renewals and repeated use
  by four of five teams over at least four weeks.
- Target a 50% reduction in median review time against a measured customer baseline.
- Record independently reviewed material corrections; do not invent counterfactual losses saved.

Exit criteria:

- Teams complete repeated reviews on actual work and can inspect discrepancies.
- Value, operating cost and support burden are measured rather than inferred from signups.
- Genuine sessions actually elapse; accelerated test clocks do not qualify strategies.
- Continue, revise or stop the commercial hypothesis based on payment, renewal, workflow value
  and onboarding economics. These targets do not guarantee funding.

## Technical and commercial constraints to carry forward

- Historical price bars cannot establish historical executable liquidity. Match the claimed
  simulation fidelity to the available data and disclose assumptions.
- Holdout controls are workflow protections. [Current limitations](phase-3.md) include owner
  access, overlapping datasets and possible pretrained-model historical contamination.
- Current [forward gates](../app/forward/service.py) measure specified readiness conditions;
  passing them is not proof of investment edge or regulatory approval.
- Signed/hash-linked evidence establishes integrity properties, not truth of source inputs.
- Prospectively observed market sessions cannot be manufactured through engineering.
- Customer-employed reviewers and an Artha-employed guardian service have different economics.
- Commercial data rights and applicable activity classification must match the actual flow.
- No new credentials, paid provider calls, outreach, purchases, publication or broker orders
  were authorized by the request to store this document. Preserve previously established
  owner authority and all existing engineering invariants.

## Dated competitive assessment and sources

The 2026-11-06 analysis found substantial advertised overlap in AI construction, backtesting,
paper trading, risk tools and explanations. A globally unique MVP was not established.
The hypothesis is a useful recurring professional workflow, not novelty from prompts,
templates or the mere presence of a human reviewer.

Primary product references used in that analysis:

- [Sensibull](https://sensibull.com/).
- [AlgoTest documentation](https://docs.algotest.in/).
- [Tradetron features](https://tradetron.tech/pages/features).
- [QuantConnect research pipeline](https://www.quantconnect.com/docs/v2/cloud-platform/research-pipeline).
- [OpenAlgo](https://openalgo.in/).
- [Composer](https://www.composer.trade/).
- [SoFi's June 2026 Composer announcement](https://investors.sofi.com/news/news-details/2026/Introducing-Composer-by-SoFi-AI-Powered-Investing-From-Idea-to-Execution/default.aspx).

Relevant dated data/regulatory references:

- [Kite API FAQ and external data-use restrictions](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs).
- [Kite historical-data contract](https://kite.trade/docs/connect/v3/historical/).
- [NSE retail-algo FAQ](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail%20Algo_03112025_NSE.pdf).
- [SEBI Research Analyst master circular, February 2026](https://www.sebi.gov.in/legal/master-circulars/feb-2026/master-circular-for-research-analysts_99571.html).
- [SEBI FY25-FY26 profitability study, August 2026](https://www.sebi.gov.in/sebi_data/attachdocs/aug-2026/1787233506209.pdf).

Refresh changing product terms and rules before implementation decisions or commercial
release. These references support the analysis, not a claim of authorization.

## Resume instructions for the next model

1. Read [AGENTS.md](../AGENTS.md), this handoff and the linked current release evidence.
2. Recheck the working tree, relevant implementation and dated deployment state. Do not treat
   the recorded fingerprint or runtime state above as permanently current.
3. When asked to continue implementation, read MVP-1 through MVP-3 and their actual records. Complete genuine off-host/public-host acceptance when the owner supplies the destination/recipient, and begin MVP-4's independent financial cases when requested.
   Work independently on items supported by available information; identify actual missing
   dependencies without creating an additional approval process for routine engineering.
4. Preserve application source/locks before source changes as required by AGENTS.md.
   Preserve account data, immutable evidence and existing live/paper boundaries.
5. Update the stage table and progress log with actual artifacts and validation evidence.
   Mark a stage complete only when its exit criteria have been met.
6. Keep commercial outcomes separate from engineering completion. Record customer evidence,
   assumptions and unresolved dependencies explicitly.

## Progress log

| Date | Work completed | Validation / next step |
|---|---|---|
| 2026-11-06 | Repository/market analysis and technical feasibility assessment; owner requested durable five-stage handoff | Documentation only; MVP-1 through MVP-5 remain NOT STARTED. Next: supported strategy and data contracts. |
| 2026-11-06 | MVP-1 implemented in the separate Strategy Contracts sidebar option; existing workflows preserved | 549 automated tests, 11 browser suites, 10 new controlled browser checks and 25 deployed read-only checks passed. Backups/restores and deployed stateless assessment passed. External customer/data acceptance pending; MVP-2 not started. |
| 2026-11-06 | MVP-2 implemented and deployed in Strategy Contracts → Research & evidence; frozen protocols, shared-engine basket evaluation, receipt clocks, retained attempts and verifiable private packets | 571 automated tests, 12 isolated browser suites, 7 new browser checks and 26 deployed read-only checks passed. Pre/post backups restored; account/cache/authority preserved. [Actual handoff](mvp-2/implementation.json). External acceptance pending; MVP-3 not started. |
| 2026-11-06 | MVP-3 implemented/deployed in separate Team workspace: authenticated roles, organization stores, independent snapshot reviews, bounded paper scopes and verified recovery | 595 automated tests, 13 isolated browser suites, 8 new two-user browser checks and 27 deployed checks passed. Real GPG and controlled SFTP/restore tested; genuine off-host target/recipient, first-admin setup and customer-host acceptance pending. [Actual handoff](mvp-3/implementation.json). No broker authority changed. |
