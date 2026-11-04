# Guided Kite connection setup

Updated: 2026-11-04. This app uses the owner's **₹500/month Kite Connect data plan**.
The subscription includes real-time market data and available historical candles; the free
Personal API does not include either. Billing remains with Zerodha. Nothing in this page
purchases a subscription, reads broker balances or enables real-money trading.

## User flow

Open `http://127.0.0.1:8787/#setup` through the existing SSH tunnel, or select
**Connection setup** in the sidebar. Settings also links to this flow.

1. In the Kite developer account, use a Connect app and register the exact redirect URL
   shown on the page. Keep the same browser host/port for sign-in. Localhost/127.0.0.1
   redirects are supported for local use; this service remains bound to loopback.
2. Paste the app's API key and API secret into the password fields and choose
   **Save privately on this server**. Fields clear after submission. Existing configured
   credentials are never displayed or prefilled. Do not enter the Zerodha account password
   or PIN here; those belong on Zerodha's login page.
3. Choose **Continue to Zerodha**. Complete the broker's normal login. The callback returns
   to setup, clears its OAuth cookie and removes the temporary request-token URL.
4. Run **Test stock quotes** and **Test historical candles**. These are separate, small
   read-only checks of a RELIANCE quote and available daily candles. They establish sample
   access, not complete coverage for every symbol, market segment or historical period.
5. Choose **Connect NSE market data**. Observe actual received/fresh/missing symbol counts.
   The app declares quotes arriving only when fresh observations are present.
6. Optionally choose **Use stock quotes on paper desk**. This selects RELIANCE, HDFCBANK and
   INFY for paper simulation, while the breadth feed covers the current equity universe.
   Change instruments or add current derivatives through Settings. Source changes require
   a flat account with no pending orders. Quote readiness and risk/pause controls remain separate.

A quote returned after market hours can confirm access while remaining stale. It does not
become fresh because sign-in succeeded or a socket connected. The page separates stored keys,
session availability, verified sample access, observed feed freshness and executable paper
quotes. Historical access can be retried independently; it does not fabricate an imported
research dataset. Clear messages cover unsuccessful sign-in, data permissions and network
failures without exposing upstream diagnostics.

## Data limits

Kite provides current instruments, live market quotes and available historical candles.
It does not supply historical expired-option candles or a complete historical order book.
Daily continuous futures data is a broker feature, not a complete expired-derivative archive;
this application's existing importer does not yet expose a full continuous-contract mapping.
Full corporate-action history, point-in-time listings, borrow and historical margin inputs
still need suitable additional evidence. Live depth and simulated execution are distinct.

Kite access tokens expire at 6 AM the next day and may be revoked earlier. Repeat normal
sign-in when requested; saved app keys need not be re-entered daily. AI generation uses a
separate model account and is not included in the ₹500 market-data subscription.

## Credential handling and precedence

The setup form uses the existing local host/same-origin JSON request boundary. Password
fields clear after submission, are never placed in URLs/localStorage and are not returned
by status or validation errors. The key is used in the broker-required login URL; the
private secret stays on the server for checksum exchange. Account passwords/PINs are not
collected. Access logging remains disabled, and browser resources use the existing CSP.

The service stores recognized configuration in `.env.local`, as a regular file owned by the
service user, with mode 600. Symlinks, broad permissions, duplicate/unknown settings and
oversized files are rejected. Writes use a private temporary file, fsync and atomic replace.
This is owner-restricted local storage, not a claim of KMS-backed encryption. Existing AI
settings are preserved. Appending shell commands to configuration does not execute them.

Explicit file values take precedence over matching environment variables. This ensures a
key replaced in the UI does not silently revert to old environment settings after restart.
Key replacement clears the old saved access token, invalidates pending OAuth state and resets
session-bound access checks. Browser-login tokens remain in memory and are not written to the
file. The configuration is outside the static web root and excluded from source archives,
screenshots and documentation. Back up real secrets only through a separate private process.

Replacing a currently used Kite feed's credentials requires no open paper positions or active
orders. New entries are paused for that active feed until the operator reviews readiness.
Saving keys while the paper desk uses demo data does not replace the demo account or its
positions. Feed connection alone never changes the paper source or sends an order.

The workspace still relies on loopback and the owner's SSH access. Do not expose these
single-user credential endpoints publicly without implementing appropriate authentication
and deployment controls. The setup page adds convenience within that existing boundary.

## Implementation and checks

- `app/configuration.py`: bounded private-file parsing and atomic persistence.
- `app/onboarding.py`: strict credential schema, secret-free status, session-bound read checks.
- `app/api.py`: onboarding routes, credential-validation redaction and fixed OAuth return paths.
- `static/onboarding.js`: guided connection flow and readiness states.
- `tests/test_onboarding.py`, `tools/onboarding_browser.py`: controlled acceptance.

Routes: `GET /api/onboarding/status`, `POST /api/onboarding/kite/credentials`,
`POST /api/onboarding/kite/check` with kind `quotes` or `history`, and
`POST /api/onboarding/kite/connect`. The page uses the existing `GET /api/kite/login?return_to=setup`
and callback. No arbitrary return URL or new broker endpoint is accepted.

CodeGuard checks cover hardcoded-secret avoidance, approved SHA-256 authentication checksum,
verified TLS, secret-free errors/status, private file permissions, safe serialization,
same-origin boundaries and credential rotation. Existing broker-only API allowlists and
backend paper execution enforcement remain intact. Controlled tests do not prove access
for the owner's real subscription; that is what the onboarding checks will verify after login.

## Primary sources checked on 2026-11-04

- [Kite plan contents and current ₹500 price](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-are-the-charges-for-kite-apis).
- [App setup and local redirect examples](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/how-do-i-sign-up-for-kite-connect).
- [Authentication and session expiry](https://kite.trade/docs/connect/v3/user/).
- [Historical candles and continuous futures](https://kite.trade/docs/connect/v3/historical/).
- [Expired futures/options limitations](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/charts/articles/historical-data-for-expired-f-o-contract).


## Deployed validation

The guided connection page is deployed at `http://127.0.0.1:8787/#setup` through the existing
SSH tunnel. **163 automated tests** and **64 controlled browser workflows** passed, including
12 onboarding workflows. The remaining browser checks cover paper trading, historical research,
market data and the Strategy lab. All five JavaScript syntax checks, dependency consistency
and documentation links passed. The known TestClient/httpx deprecation warning is unchanged.

A read-only check of the actual deployed page verified the plan guidance, exact redirect URL,
empty credential inputs, appropriate authentication gates and mobile layout. No real app keys
were entered during deployment, and no real broker login is claimed by the controlled tests.
Database backups and a verified source/asset archive were saved. Main account cash, orders,
reconciliation and existing candidate versions were preserved; new paper entries remain paused.

- [Release validation](onboarding-validation.json).
- [Controlled onboarding browser checks](onboarding-browser-validation.json).
- [Actual deployed browser checks](onboarding-deployed-browser.json).
- [Deployment and backups](onboarding-deployment.json).
- [Deployed setup page](screenshots/onboarding-deployed-desktop.png).

Enter your real key and secret only on the setup page and complete Zerodha's login there.
The page will verify your own subscription's data access; source/API credentials never need
to be shared in chat. No live-order path is installed.

## Owner connection verification — 2026-11-04

After the deployment above, the owner configured a paid Connect app through the setup
page and completed Zerodha browser login. Both genuine sample checks passed: a RELIANCE
quote and ten daily historical candles. The authenticated breadth feed was then enabled;
all three WebSocket connections connected, with actual observations received and REST
snapshots supplementing symbols beyond the streaming allocation. See the timestamped
[real connection verification](kite-connection-validation.json) for measured coverage,
freshness and transport status. Counts are observations at that time, not service guarantees.

An earlier login-state rejection was followed by a successful fresh login. Its precise
cause has not been established; browser-cookie/state validation remains enforced and no
authentication source change was deployed. Do not retain callback URLs with request tokens.

The selected paper desk remains on DEMO with entries paused, virtual accounting reconciled
and real-money execution disabled. This proves provider connectivity and sample data access,
not complete historical coverage, active-market forward execution, or strategy qualification.
Dedicated AI configuration and real-data/economic acceptance remain separate outstanding work.
