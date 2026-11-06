# MVP-1 data contract

Recorded 2026-11-06. Requirements are defined independently from evidence that a source
actually supplies them. These requirements do not grant distribution rights.

| Requirement | Contract / observation | Current assessment |
|---|---|---|
| Stable identities | Underlying INDEX ID plus each exact NFO option ID | Matched against immutable manifest; broker tokens alone are insufficient history |
| Option terms | CE/PE, strike, common aware expiry, lot/tick sizes and effective dates | Matching terms effective by entry; later-known terms cannot replace them |
| Money / units | Integer paise, positive whole lots and dated units/lot | Exact contractual arithmetic; no monetary floating-point ledger |
| Decision clock | One aware entry and exit on the same IST session date | Explicit scheduled protocol; no natural-language inference |
| Calendar | Dated session, holidays and special sessions with a review reference | Dataset session checked; review declaration is not independent exchange acceptance |
| Price fidelity | QUOTE or MINUTE explicitly selected | Frequency must match; candles cannot supply historical bid/ask liquidity |
| Quote observations | Bid/ask, quantities, exchange timestamps and required freshness | Selected dataset boundaries checked; no continuous coverage or queue guarantee |
| Receipt provenance | Original exchange time and first receipt/availability time | UNVERIFIED: current catalog rows do not prove both clocks |
| Costs | Effective dated fee rules plus declared budget estimate | Rule availability checked; actual fees require independent source review |
| Source use | Provider, coverage note, intended use and agreement reference | Owner assertion only; commercial acceptance remains pending |
| Revision integrity | Dataset ID, raw hash and Parquet hash | Verified before assessment and retained in saved evidence |

For each decision boundary, eligible observations have timestamps no later than the
decision and age no greater than max_quote_age_seconds. For option legs in QUOTE mode,
both displayed sides must cover at least max(strategy lots, minimum depth lots) times
the dated units per lot, and the percentage spread must fit max_spread_bps.
This is a compatibility check, not an execution simulator.

Data-ready status requires all DATA findings to pass. It remains false while receipt-time
evidence is unverified. Do not flip a declaration into a pass without an implemented,
reviewed provenance contract and source adapter.

## Source acceptance checklist

Record the provider and agreement reference without uploading credentials. Establish:

- Covered historical contracts, expired options, time range, sampling and known gaps.
- Original timestamp, receipt time, bar-close semantics and point-in-time availability.
- Dated contract master and cost schedules.
- Corrections/revisions and immutable source artifacts.
- Whether local research, client research, display, derived data and redistribution are
  permitted for this actual deployment.
- Genuine sample acceptance with missing/crossed/stale observations, lot/tick changes,
  holidays and incomplete legs exercised.
- Measured latency/completeness and who accepted each limitation.

## Primary references checked for this implementation

- [NSE NIFTY specifications](https://www.nseindia.com/static/products-services/equity-derivatives-nifty50).
  Load dated terms for the actual contracts; example lot sizes/expiries are fictional.
- [Kite historical data](https://kite.trade/docs/connect/v3/historical/).
  Historical candles and live instrument metadata are not a complete expired-options
  quote/depth archive.
- [Kite API and data-use FAQ](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs).
  Personal API access does not establish external display or redistribution authority.

Refresh changing rules and agreements before release. The implemented source selector
makes no external requests and purchases no data.
