# MVP-3: team identity, approval and recovery

This delivers the third stage of the [B2B roadmap](../mvp-validation-roadmap.md), extending
[MVP-1](../mvp-1/README.md) and [MVP-2](../mvp-2/README.md). It is separate from the older
engineering Phase 3 AI candidate work. Actual final results belong in
[implementation.json](implementation.json).

Open **Team workspace** in the sidebar. It opens /team in a separate tab. The existing
local-owner views, Strategy Contracts tabs, accounts and broker controls remain available.

Engineering implemented and deployed 2026-11-06. Actual checks passed: 595 automated
tests, 13 isolated browser suites and 27 deployed read-only checks. First-administrator
setup and genuine off-host/customer acceptance remain required.

## Initialize the organization

No default account/password is installed. The first administrator chooses their own
password. For the existing local-owner deployment, either enter its owner-only one-time
setup key in the team setup form, or initialize it through a private terminal prompt:

```bash
.venv/bin/python -B tools/team_admin.py setup   --root data/teams/default   --email admin@example.com   --organization "Research team"
```

Passwords are prompted privately; they are never command-line arguments or printed.
The HTTP setup form requires the random key in data/teams/default/bootstrap.key, created
with owner-only permissions. Successful setup consumes that key. It is not returned by
an API, placed in a URL or included in backups.

The initial administrator receives all three roles so they can prepare research and
manage membership. Contributor separation still prevents them from reviewing their own
work. Invite another person through **Members**. Assign only the required roles, share the
one-time invitation link directly, and let the invitee choose a password. Invitations
expire after 24 hours, are single-use and are not emailed automatically.

**Issue new invitation** invalidates an existing member's sessions/authority and supplies
a new password-setting link. The last active administrator cannot be disabled or reset
this way. A trusted local operator can recover an existing administrator:

```bash
.venv/bin/python -B tools/team_admin.py recover-admin   --root data/teams/default --email admin@example.com
```

That action is audited as local filesystem operator recovery. It requires filesystem
control, resets the password through a private prompt and clears sessions.

## Roles and research flow

| Role | Authority within this organization |
|---|---|
| Researcher | Import supported data/definitions, freeze protocols, build initial evidence, capture packets and use an approved bounded scope |
| Reviewer | Read evidence, comment, review another contributor's packet, grant scopes and revoke scopes |
| Administrator | Read evidence, invite/manage members, revoke scopes and create verified local backups |
| Any authenticated member | Read organization evidence and change their own password |

Members may hold multiple roles. Server checks enforce the roles, active membership and
credential version on every authenticated action. An administrator's role does not
override contributor separation. Separation is enforced between distinct authenticated
accounts; the organization's administrators remain responsible for who controls those
accounts. Email ownership, professional credentials and natural-person independence are
not independently verified.

1. In **Research**, explicitly load the fictional example or import a quote-dataset JSON
   envelope and an MVP-1 strategy definition. Imports create organization-owned revisions;
   an exported owner's historical assessment is reassessed.
2. Select the definition/dataset, virtual capital, cutoff and attempt budget. Acknowledge
   incomplete source/customer evidence and freeze the protocol. Inspect its strategy,
   risk policy, execution assumptions and authenticated author context.
3. Build the initial baseline/reproduction/stress evidence using the shared research
   engine. Accounts, catalog, jobs and journals are isolated from the owner's workspace.
4. Capture an immutable packet. From this point, additional execution for the protocol
   requires an independent operating scope.
5. A reviewer opens **Reviews & scopes**, selects the exact packet and records rationale
   plus ACCEPT, REQUEST_CHANGES or REJECT. Authors of the saved definition, protocol,
   included attempts or capture cannot review that packet.
6. ACCEPT records a review of the fixed snapshot only. It authorizes no additional run.
7. An eligible reviewer separately grants a **BOUNDED_PAPER_RESEARCH_ONLY** scope. It binds
   the exact protocol, definition, dataset/Parquet hash, engine, risk policy, permitted
   scenarios, expiry and maximum new attempts. Unqualified research requires an explicit
   acknowledgment. Expiry is at most seven days; the frozen protocol budget still applies.
8. A researcher selects that scope to request further execution. The server reserves
   one durable operation identity; retries reuse it. Invalid bindings/scenarios and revoked
   scopes fail before consuming a new reservation.
9. Reviewers or administrators can revoke a scope with a reason. Membership/credential
   changes, expiry, recovery generation changes and superseding snapshot decisions also
   invalidate affected authority. Workers check authority before each subsequent replay
   batch and cancel when it no longer passes.

Cancellation retains committed partial fills/costs; it does not fabricate a completed
exit or final valuation. This remains simulated research. There is no team broker-order
route or automatic prospective activation.

A scope remains bound to the reviewed snapshot as a historical record while its bounded
research attempts are consumed. New attempts do not themselves require a new grant for
every observation. Material strategy, protocol, dataset, build or risk changes require
new bound evidence/review. Superseding the accepted review invalidates its old scope.

Use **Refresh workspace** to load another member's new protocols/reviews. Active research
attempts refresh automatically. Default exports contain aggregate evidence; quote-bearing
traces keep the explicit permitted-private-use acknowledgment and source-reference checks.
The existing offline verifier accepts the extended authenticated evidence format.

## Organization isolation and deployment

Each team service has exactly one server-generated organization ID and one private data
directory. The server derives identity/organization from the authenticated session/store;
browser-entered organization labels and headers cannot select another tenant.

The embedded /team route is for the existing local owner. The owner's legacy APIs retain
their earlier local-user trust boundary. **Do not share the local-owner service as a team
deployment.** A customer deployment uses the organization-only application:

```bash
.venv/bin/python -B tools/team_server.py   --root /var/lib/artha-teams/acme --port 8788   --public-host research.example.com
```

Use a dedicated directory, listener/host, operating-system account and TLS reverse proxy
per customer. Do not point a team server at the owner's data directory, or run multiple
web servers against one organization store. The standalone application has no owner
account, broker, generic research-job or legacy static routes. Its listener binds only
to loopback; explicit trusted public hostnames and HTTPS forwarding are required for a
public proxy. Forwarded headers are trusted only from loopback. No CORS allowlist opens
the API to unrelated origins.

[Service template](../../deploy/artha-team@.service) expects code in /opt/artha,
a dedicated artha-team-INSTANCE account and an owned /var/lib/artha-teams/INSTANCE directory.
Set a unique ARTHA_TEAM_PORT and ARTHA_TEAM_PUBLIC_HOST through
/etc/artha/team-INSTANCE.env before enabling a customer instance. The template is supplied;
this implementation does not create public customer hosts or accounts.

Identity uses salted scrypt with N=2^17, r=8, p=1, bounded concurrent hashing and passwords
of 15–256 characters. Sessions use random opaque cookie credentials whose hashes are
stored server-side, HttpOnly/SameSite cookies, HTTPS Secure cookies, session CSRF tokens,
origin/header checks, an eight-hour absolute expiry and a 30-minute timeout between
authenticated requests. Foreground polling counts as request activity. Membership and
password changes invalidate sessions; logout deletes the current server-side session.
Login throttles persist per account/peer, and active sessions are bounded per member.
There is no SSO or MFA integration in this stage.

These choices use the profiles described by
[OWASP password guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
and [session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
They are not a claim of a completed independent security assessment.

## Backups, retention and recovery

Administrators can create verified local snapshots under the organization's backups
directory. A shared thread/file lock serializes authority mutations across web/operator
processes. Research workers pause at committed checkpoints; they remain paused afterward.
SQLite online backups capture consistent databases, actual dataset artifacts and source
builds needed by the protocols. Keep prior build archives before every software upgrade.

Snapshots omit setup keys, plaintext passwords, sessions, unused invitation credentials,
broker configuration, SSH keys and GPG private keys. They include sensitive password
verifiers and research data; directories/files are private. Default retention keeps seven
verified managed snapshots, with a minimum configurable keep count of two. Unrelated or
unverified directories are not removed.

```bash
.venv/bin/python -B tools/team_ops.py archive-source --root data/teams/default
.venv/bin/python -B tools/team_ops.py backup --root data/teams/default --keep 7
.venv/bin/python -B tools/team_ops.py verify /path/to/team-TIMESTAMP-ID
.venv/bin/python -B tools/team_ops.py restore /path/to/team-TIMESTAMP-ID   --destination /new/isolated/organization
```

The source archive command is for preserving the current complete source before an
upgrade; an existing immutable archive is never overwritten. A customer instance needs
its prior archives in its own releases directory when they are no longer available from
the owner deployment's release archive.

An actual off-host destination and recipient were not supplied during implementation.
The encrypted delivery pipeline is implemented and tested using real GPG encryption and
an isolated controlled SFTP transport; no production data has been copied to an inferred
external host.

After the operator configures a dedicated SSH host alias, pins its host key, installs the
authorized public GPG recipient locally and independently checks its full fingerprint:

```bash
.venv/bin/python -B tools/team_ops.py publish /path/to/team-TIMESTAMP-ID   --host APPROVED_SSH_ALIAS --directory /approved/backup-directory   --recipient VERIFIED_40_HEX_FINGERPRINT --keep 7
```

The tool encrypts with the explicit recipient, does not retrieve keys over the network,
uses noninteractive SSH with configured identities and strict host-key checks, uploads through a partial filename
and reads back the encrypted archive to verify its hash before recording success/retention.
Only managed archives for that organization's remote subdirectory are eligible for pruning.
See [GnuPG's operational documentation](https://www.gnupg.org/documentation/manuals/gnupg/Operational-GPG-Commands.html).

For scheduled delivery, configure the approved target in /etc/artha/team-backup-INSTANCE.env
using ARTHA_BACKUP_HOST, ARTHA_BACKUP_DIRECTORY and ARTHA_BACKUP_RECIPIENT, then explicitly
enable the supplied [backup service](../../deploy/artha-team-backup@.service) and
[timer](../../deploy/artha-team-backup@.timer). The backup-publish command performs the same
verified snapshot and encrypted publication sequentially. Templates are not enabled by default.

Restore a retrieved encrypted archive using the matching private key through the operator's
GPG agent:

```bash
.venv/bin/python -B tools/team_ops.py restore-archive /private/retrieved.tar.gpg   --destination /new/isolated/organization
```

Automatic encrypted restoration is bounded to 2 GiB of decrypted archive data and 20,000
regular-file entries. The archive is checked for traversal/link hazards, extracted into a
private temporary directory and verified before restoration. Larger stores need an
operator-reviewed recovery plan rather than assuming this bounded helper accepts them.

Restore refuses an existing destination. It preserves evidence, removes sessions/invitations,
increments the organization recovery generation and keeps unfinished jobs paused. Old operating
scopes cannot resume operation in the recovered store. Reconcile/review the preserved work and
issue renewed independent scope before continuing. It never replaces the original account data
or imports broker credentials.

## API and evidence

Authenticated routes live under /api/team: session/login/logout/password, invitations and
membership, dashboard, datasets/contracts/protocols, trials/control/recover, packets/export/
verify, comments/reviews/scopes/revocation and backups. Guest access is limited to the
setup, invitation-acceptance and sign-in flow. Request validation does not echo submitted
values. Dataset envelopes use a 34 MB request bound; verification uses 16 MB; other
mutations use 16 KB.

See [review schema](review.schema.json), [scope schema](scope.schema.json),
[membership schema](membership.schema.json), [architecture decisions](design.md) and
[acceptance inventory](acceptance.md). Identity and review ledgers are locally hash linked;
they are not external identity attestations or signatures. Evidence hashes do not certify
source truth, source licensing, investment edge or regulatory approval.

Optional AI explanation is not required to create or review deterministic team research.
No existing candidate, forward or broker qualification gate was relaxed. In particular,
the old genuine-AI-monitor gate remains unchanged for its original forward qualification
workflow. This is an explicit separation of research validity from later forward authority.

[Workspace validation](../workspace-validation.json), [controlled team browser evidence](../team-browser-validation.json),
[deployment](../workspace-deployment.json) and [deployed UI checks](../workspace-deployed-browser.json)
record actual outcomes. External source/customer acceptance, a real off-host delivery/restore,
public TLS deployment acceptance and independent financial/security acceptance remain open.
