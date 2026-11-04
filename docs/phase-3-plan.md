# Phase 3 implementation baseline

Phase 3 delivers bounded AI candidate authoring, reproducible evaluation and an immutable
strategy registry. Real-money execution and unattended forward trading remain disabled.
"Industry best" is a quality target requiring external benchmarking and operating evidence;
it is not a release claim established by local tests.

1. Immutable study protocol: dataset version, chronological train/validation/holdout splits,
   embargo, execution/risk assumptions, research thresholds and explicit request/run budgets.
2. Immutable candidate versions, lineage, schema/capability checks and retained failed attempts.
3. Genuine OpenAI Responses connector with dedicated server credentials, structured output,
   training-only context, no tools or arbitrary generated-code execution, usage evidence,
   bounded requests, cancellation and honest unconfigured/error states.
4. Durable evaluation orchestration through the existing isolated research workers: training,
   repeatability, validation, doubled-cost stress, walk-forward and parameter sensitivity.
5. Finalist freeze and single-use holdout within a dataset lineage; no adaptive model feedback
   from holdout; failed/interrupted holdout attempts remain consumed and visible.
6. Evidence-based reports, comparable-trial search diagnostics, data/model warnings and
   explicit acceptance checks. Human data/model review and paper approval create a packet;
   approval never activates trading or guarantees performance.
7. Strategy lab UI covering studies, AI/manual authoring, registry versions, evaluations,
   comparisons, lineage, rejection/retirement, review, audit and export.
8. Security, negative-path, replay, persistence, accounting isolation and browser acceptance;
   backed-up deployment and owner handoff with all missing external validation reported.

Current product limits remain explicit: supported declarative families only, no exchange
queue simulation, estimated margin where configured, no independently verified trading edge,
no claim that historical LLM training is free of future-event contamination. Market-data
and AI credentials are separate; a working connector is not evidence of a subscribed session.


Delivered artifacts and exact operational limits are mapped in [phase-3.md](phase-3.md).
Measured results: [release validation](phase-3-validation.json) and
[deployment evidence](phase-3-deployment.json). Genuine provider and independent financial
acceptance remain pending; controlled test success is not provider or trading qualification.
