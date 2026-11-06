# MVP-3 architecture decisions

## Data and authority boundaries

The existing local owner retains its original application and account trust boundary.
A separate Team workspace link opens the authenticated team surface. Commercial/customer
operation uses the standalone team-only app, one organization/store/listener/OS user per
deployment. It does not mount owner, broker or generic research routes.

Identity, organization ID and roles come from the private identity store and opaque session.
Client organization_ref, display names and headers cannot establish team authority.
Review/protocol/attempt/capture identities are immutable content bound to authenticated
server context. Each store has its own SQLite databases, research catalog and run directories.

The existing ContractBook, ContractResearch and Research engine accept optional server-only
identity/authorization context. Legacy callers retain their behavior. Authorization metadata
is bound to the durable attempt/run identity but kept outside the economic configuration,
so identical declared financial inputs still reproduce the same economic hash.

## Snapshot decision versus operating scope

A SNAPSHOT_REVIEW records fixed packet identity/hash, contributor identities, reviewer,
decision, rationale and time. Any contributor in the packet's definition/protocol/attempt/
capture chain is excluded from approving it. Administrator status does not override this.

A separate SCOPE_GRANT binds exact protocol/strategy/source/build/risk identities,
permitted scenarios, expiry, attempt budget and organization recovery generation.
Scope authority is BOUNDED_PAPER_RESEARCH_ONLY. Accepting a packet alone grants no operation.
After the first capture, subsequent team execution requires a valid independent scope.

EXECUTION_RESERVED records a durable idempotent operation before dispatch. Cross-process
file locking serializes authority changes/reservations and backup operations. Shared research
workers load authority read-only from paths derived from their organization-owned research
root, recheck each replay batch, and cancel with committed partial evidence on failure.
No authorization file path comes from a browser strategy.

Expiry, revocation, superseded reviews, changed identities/roles, changed bound economic
inputs/builds and recovery generation changes fail closed. New quotes inside a frozen
evaluation are not new strategy approvals. Changed material configuration requires new
bound evidence and approval. Snapshot history remains inspectable after additional trials.

## Persistence and recovery

Identity tables contain organization settings, salted password verifiers, memberships,
hashed sessions/invitations, persistent throttles and a hash-linked identity ledger.
The organization workspace reuses MVP-1/2 tables and adds immutable team_review_records,
idempotency requests and a review ledger.

Verified snapshots lock mutations and pause workers, use SQLite backup rather than copying
live WAL files, include actual datasets and exact source builds, and exclude session/invite
credentials. Retention deletes only verified managed snapshots for the same organization.
Off-host publication encrypts to an operator-pinned GPG fingerprint, uses noninteractive
SFTP with strict host-key checks and verifies an encrypted readback. No target is inferred.

Restoration uses a new isolated directory. It verifies records/artifacts/source identities,
invalidates sessions and invitations, increments recovery generation and leaves unfinished
research paused. The original financial/account stores are never replaced. Source authority
must be reviewed again before continuing an old bounded operation.

## Explicit AI qualification decision

Deterministic research validity and account-based review do not require an LLM response.
The team app makes no provider calls, grants no forward/broker activation and does not
reuse application or agent credentials. Existing forward genuine-AI-monitor, candidate,
source and pilot gates remain in place. Optional explanation can be added later without
turning model output into authorization.

## Remaining acceptance

Authenticated account separation is not proof of different natural persons or professional
credentials. The organization's administrators control invitations and account recovery.
No SSO/MFA, third-party identity proof, public multi-tenant hosting, independently anchored
audit signatures, actual customer acceptance or independent security certification is
claimed. Real off-host target, recipient, TLS/customer deployment and independent financial
acceptance require genuine operator/customer evidence.
