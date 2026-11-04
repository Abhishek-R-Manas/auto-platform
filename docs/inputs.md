# Dated research inputs — release 0.6.0

Open **Forward paper → Research inputs** at http://127.0.0.1:8787/#forward.
The workspace supports official daily institutional reports, automatic public macro data,
RBI release notices and estimated IV/skew from the existing Kite quote connection.
All accounts and executions remain paper-only. No additional broker provider is installed.

## Sources and actual availability

| Input | Transport | Frequency and interpretation |
|---|---|---|
| FII/FPI and DII cash activity | Import an official NSE CSV | Daily provisional buy, sell and net values in INR crores; NSE-only and combined NSE/BSE/MSEI reports remain separate |
| CPI | Official MoSPI machine-data endpoint | All-India Combined General index and reported inflation; current base year comes from official metadata |
| IIP | Official MoSPI machine-data endpoint | Monthly General index and reported growth; its base year and category code come from metadata |
| GDP | Official MoSPI machine-data endpoint | Quarterly GDP growth at constant prices; fiscal quarters are mapped to their actual calendar end dates |
| RBI policy/release notices | Official RBI RSS | Dated informational notices; no policy rate is guessed from text |
| IV, skew and Greeks | Existing Kite quote API | Bounded Nifty/Nifty Bank chain snapshots, modelled from bid/ask; available only with authenticated fresh quotes |

The implemented MoSPI connector uses the official public endpoint at
https://mcp.mospi.gov.in/. The portal's [machine-access documentation](https://esankhyiki.mospi.gov.in/llms.txt)
advertises it. The application executes only fixed read-only statistical operations using
HTTP; no model receives tools or credentials. It discovers indicators, base years and filter
codes, then validates the returned table's country/sector, unit and price basis. Metadata,
filters and raw replies remain in the immutable artifact. Secure access to the legacy MoSPI
API failed on this host; TLS verification was retained and the working official interface is used.

The original CPI/IIP/GDP release timestamps are absent from those returned tables. They stay
unknown. The application records the server's first receipt as the earliest known availability.
RBI sometimes supplies dates without a timezone or an exact time: an explicit Asia/Kolkata
assumption and date-only precision remain visible. The original RSS stays retained.

The real public-source validation is recorded in [inputs-provider-validation.json](inputs-provider-validation.json). It is
separate from the controlled browser/quote fixtures. Actual fresh Kite-chain validation still
requires the owner's normal login during a market session; software tests do not establish it.

## Daily FII/DII report import

1. Download the intended report from the [official NSE page](https://www.nseindia.com/reports/fii-dii).
2. Choose **NSE FII / DII**, select **NSE cash only** or **NSE + BSE + MSEI cash**, and retain
   the official publication URL. Do not merge those two scopes or replace cash activity with
   a differently defined depository/investment dataset.
3. Upload the CSV. The native category/date/buy/sell/net headings and canonical header template
   are accepted. Comma-formatted numbers are parsed. Net must reconcile to buy minus sell.
4. Supply a publication timestamp with timezone only if stated by the source. Otherwise leave
   it blank. Record the reviewer and confirm permitted local use, units and scope, then import.
5. An identical report is idempotent. Changed source content creates another retained artifact;
   earlier values remain available for historical cutoffs. Missing categories are not zero-filled.

FII/DII is daily provisional activity, not a real-time classification of institutional orders.
The [NSE website terms](https://www.nseindia.com/static/nse-terms-of-use) restrict systematic
website collection. This release provides report import, not an undocumented website scraper.
Unattended external FII/DII delivery requires an appropriately authorized data interface and
its actual contract/access details. Local-use confirmation is an owner assertion, not verified
redistribution permission or regulatory authorization.

## Automatic updates and option surfaces

The settings control periodic macro updates and Kite analytics. Public macro refresh defaults
to 3,600 seconds when enabled. Kite refresh defaults to 10 seconds during weekday 09:15–15:30
IST windows. Those windows are a scheduler bound, not an exchange-calendar claim: holidays,
closed markets and missing sessions still fail quote freshness. Manual refresh is available. Keep the quote refresh interval comfortably below the 30-second
quote validity limit when continuous forward dependencies are selected. Public macro requests
run independently and cannot occupy the Kite worker.

Kite analytics uses current master contracts and central broker quote pacing. Default scope is
Nifty 50, two nearest current expiries, five strikes on each side of ATM; Nifty Bank and up to
three expiries are selectable. The total snapshot is capped at 500 contracts. Separate raw
market-data subscriptions and broker execution are not added.

The model inverts European spot Black–Scholes using fresh bid/ask midpoints and declared annual
interest/dividend assumptions. Quotes with wrong tokens, missing/integral-invalid depth,
crossed/wide spreads, future/stale times or excessive option/underlying timestamp skew are
rejected. Bid/ask IV ranges and per-contract Greeks remain available for inspection. Delta
brackets are interpolated without extrapolation; absent brackets leave skew unavailable.
COMPLETE means the requested skew metrics are calculable, not that every exchange contract
was observed. Every surface displays its rejected contracts, source quote expiry and stale state.

25-delta skew is **put IV minus call IV**, in volatility percentage points. ATM IV is in percent.
Vega is per one percentage-point volatility move; theta follows the existing per-calendar-day
model. These are estimates, not exchange-supplied Greeks, a calibrated volatility surface,
portfolio margin, executable basket prices or a return guarantee. Rates are declared model
assumptions, not automatically inferred from RBI notices.

## Using dated inputs in strategies and AI

The available numeric names include:

- fii.COMBINED_CASH.net and dii.COMBINED_CASH.net; use NSE_CASH for NSE-only reports.
- macro.CPI_INDEX.2024 and macro.CPI_YOY.2024 for the observed current CPI base.
- macro.IIP_INDEX.2022-23, macro.IIP_YOY.2022-23 and macro.GDP_YOY.2022-23 for those bases.
- iv.NIFTY.near.atm_pct, iv.NIFTY.near.skew25_pp and iv.NIFTY.near.butterfly25_pp.
  next/third and BANKNIFTY names exist when their corresponding data is calculable.

Actual base years/names are discovered from the source; never relabel an older base as a new
one. Select one to twenty names and **Use selected inputs in new experiment**. The new protocol
fixes evidence_features and evidence_policy_hash. Missing/stale values, a changed input policy,
or an unhealthy input supervisor/audit block that portfolio's entries. Deterministic protective
controls continue. Existing portfolios are not silently edited to use new economics.

Completed bars receive only features available by their own timestamp. Daily FII facts cannot
appear earlier in that day's decisions. Signal events retain exact observation/artifact ids and
the policy hash. RULES strategies can use the existing named-feature expression contract.
Bounded AI monitoring receives dated numeric facts and a small set of release notices; advice
remains advisory. Guardian report hashes include the current selected input evidence, so a
source revision invalidates an approval of an older snapshot.

Historical enrichment creates an immutable child of an existing dataset (up to 50,000 rows per
operation), retaining its lineage and source limitations. A later download never fills earlier
observations. A newly collected feed will therefore often add zero values to older datasets.
Historical AI authoring receives only the enriched training rows and their valid features;
sealed validation/holdout data is not added to the model context.

## Persistence, failures and limits

Data is stored in data/intelligence/evidence.sqlite3 with SQLite WAL/FULL. Raw-source SHA-256,
artifact/observation hashes and a local event chain are verified. This is a local integrity
mechanism with no external trust anchor. All-store backup and restore-check include these
artifacts and verify their observation/event copies.

A source failure is visible while earlier evidence remains retained. Values are usable only
within their declared validity bounds: FII defaults to 96 hours from its trade-day close;
manual monthly macro reports use publication-based bounds, and automatic macro snapshots use
reference-period bounds (90 days for monthly inputs, 180 for quarterly GDP). These bounds are
not proof that no newer release exists. Source access, last job and data availability are shown
separately. Required portfolios pause when data becomes unavailable or stale.

One public-data worker and one independent Kite worker, four queued/running source jobs, 10,000 report rows, 20,000 artifacts and
250,000 observations are protective ceilings. New ingestion stops at capacity; data is not
silently deleted. Review storage, retention and offsite archival before long continuous
operation. The dashboard lists at most 200 numeric series; larger inventories report that cap.
A 90-second job deadline discards late output; HTTP responses are size bounded, verify TLS and
reject redirects/proxy settings. Read-only calls may be scheduled again at the declared interval.
Restart marks uncertain source jobs interrupted; source history stays intact. Service shutdown
has a 180-second grace window for bounded provider workers.

API entry points are /api/intelligence/status, /policy, /import, /refresh, /jobs, /observations,
/surfaces, /artifacts, /audit and /datasets/enrich. Mutations retain same-origin and strict input
controls. Official publication URLs cannot include credential query parameters; submitted
report/config validation inputs are not echoed. No new secret or paid-provider configuration
is required for the public macro sources.

## Measured delivery and remaining owner steps

Release 0.6.0 is deployed at the local address above. Hourly public macro updates and the
10-second market-window Kite schedule are enabled. Real-source startup collected 37 MoSPI
observations and 10 RBI notices, exposing five current numeric macro series. The source
artifact/observation audit passed. No controlled fixture was imported into the production store.

Validation passed 248 automated tests, 96 controlled browser checks across the application and
nine deployed desktop/mobile checks, with no browser JavaScript errors. The post-activation
snapshot contains 17 databases and 483 immutable research artifacts; its isolated restore check
also verified the new input store. The deployed build hash matches the archived tested source.
The paper account, paused state and saved private configuration were preserved.

To complete genuine institutional/option data collection, import the intended official daily
FII/DII CSV with its scope and local-use review, then sign in through the existing Kite setup
and calculate IV/skew during a fresh market session. Those two genuine-data checks remain
pending; neither test fixtures nor an authenticated session alone establish quote coverage.

Evidence: [automated validation](inputs-validation.json),
[controlled input browser checks](inputs-browser-validation.json),
[real public-source probe](inputs-provider-validation.json),
[deployment and account preservation](inputs-deployment.json),
[deployed browser checks](inputs-deployed-browser.json), and
[all-store backup/restore](inputs-backup-validation.json).
Current customer roles, verified professional licensing, commercial data rights and live
execution remain separate release dependencies. Work was performed directly without skills
or external tracking.
