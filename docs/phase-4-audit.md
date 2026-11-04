# Phase 4 end-to-end audit — 2026-11-04

Scope: trace 4A–4F from protocol creation and candidate activation through data, execution,
risk, supervision, advisory AI, human review, qualification, restoration and deployed UI.
Work performed directly without skills or external tracking. The workspace has no Git
metadata; the original build is preserved under data/releases/b06701f319e43319ee41485f497fe06594b86bbf13b94fe4e4f91525845ee0b2/.

| Phase | Reviewed path | Corrections and evidence |
|---|---|---|
| 4A | Current master, bindings, completed candles, receipt/exchange clocks and onboarding | Added independent completed-candle gap detection; retained real-provider validation as a separate requirement |
| 4B | Immutable registry/reference protocols, completed signals, shared accounting, depth and exits | Installed full quote batches before checking derivative references; pause/restart now revoke pending daily entry instructions |
| 4C | Entry and held-position risk, options sensitivities, stress and accounting | Continuous gamma/theta/vega/correlation/joint-stress checks; quote-mark return and persistent interbar drawdown; explicit stress qualification |
| 4D | Durable jobs/incidents, service/watchdog, backup and restoration | Dispatch after reservation/RUNNING commits; worker deadlines and discarded late output; recovered job incidents become resolvable; guardian restoration checks |
| 4E | Bounded advisory-only model jobs, budget, failure handling and configuration | Daily and pending budgets inspect all persistent AI attempts instead of the 200-row display window; failed/uncertain reservations retained |
| 4F | Dashboard, benchmark, qualification, exports and human decisions | Benchmark-observation and current-build recovery gates; captured, expiring, revocable human reviews bound to an evidence hash and chained event copy |

The running host initially served an older application: /api/forward/status returned 404 and
artha.service was not installed. Deployment evidence belongs in phase-4-deployment.json;
read its actual service, backup, restoration and account-preservation results. The first
deployment installed the service but its evidence step exposed an outdated onboarding URL
and response-field assumptions. Release 0.5.1 corrects that helper and adds an API contract
regression against the real onboarding response.

## Regression and acceptance evidence

Seventeen additional automated regressions cover dispatch/commit boundaries, model recovery,
hung candle workers and discarded late output, missing candles despite fresh quotes, revoked
daily instructions, quote arrival ordering, current marked returns/interbar drawdown, guardian
idempotency/revocation/expiry/snapshot drift/tampering/financial-clock preservation, persistent
budgets, continuous options risk and restoration of guardian evidence. See tests/test_forward.py.

The full active suite and six browser journeys are recorded in phase-4-validation.json and
the linked browser artifacts. Tests use isolated accounts, accelerated clocks and labelled
controlled providers. JavaScript syntax and installed dependency consistency are also checked.
The preserved release tests are excluded from normal discovery by pytest.ini.

## Centaur product direction

The forward workspace now exposes the options-architect/human-guardian workflow and the
Sandbox → 1,000-user community target → institutional/pre-seed/10,000-user target roadmap.
The guardian records conflicts, reviewer assertion, exact evidence, decision and expiry.
It cannot approve technical failures, grant live authority or silently approve later evidence.
The roadmap describes goals, not achieved user counts or investment performance. See
commercial-strategy.md for the pitch narrative, evidence expectations and primary sources.

## Remaining external and commercial dependencies

- Actual elapsed healthy market sessions, strategy samples and after-cost prospective results.
- Genuine configured model access/monitoring and any authorized external alert destination.
- Licensed, point-in-time FII/DII, macro release/revision and dedicated IV/skew feeds. Current
  option estimates use bound broker quotes; no missing feed is represented as connected.
- Entity/activity classification, professional registration/qualification verification,
  broker/algo-provider arrangements, commercial data permissions and customer obligations.
- Authenticated customer identity, roles, tenant isolation, applicable user assessments and
  broker execution for a later authorized pilot. The current local owner is not a verified RA.
- Measured multi-user capacity, retention/reviewer economics and offsite recovery. Local
  backups remain in the host's failure domain; this single-host service is not HA.

These remain explicit release dependencies. Paper software validation establishes neither a
trading edge nor regulatory authorization, 99% automation, institutional certification, a
premium valuation or funding readiness.

The final drill-pack regression requires feed loss, model failure, accounting reconciliation,
partial option legs and missing official settlement. The 14-case pack explicitly disables real
model credentials in every isolated service and preserves the owner checkpoint.

## Installed release evidence

Release 0.5.2 is installed at http://127.0.0.1:8787/#forward. The managed service, 40-second watchdog
and daily backup timer are active. The final pre-deployment snapshot verified
16 SQLite databases and 398 immutable artifacts. Original financial state,
ledger/fill/archive counts and private Kite keys were preserved. Entries remain paused and
the memory-only daily session needs sign-in. The installed browser passed ten read-only checks,
including mobile overflow and matching build identity; no owner portfolio was created.
See phase-4-deployment.json and phase-4-deployed-browser.json for measured results.
