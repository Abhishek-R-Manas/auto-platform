# MVP-3 acceptance inventory

| Boundary | Executable evidence |
|---|---|
| One-time setup, opaque session, HttpOnly/Secure/SameSite cookie, CSRF and logout | test_authentication_csrf_logout_and_secure_cookie |
| Salted password storage and one-use invitations | test_passwords_are_hashed_and_invitation_is_single_use |
| Role enforcement and last-admin recovery boundary | test_role_matrix_and_last_admin_protection |
| Server-derived author/organization | test_organization_authorship_ignores_untrusted_context |
| Contributor self-review rejected despite admin/reviewer roles | test_snapshot_acceptance_is_independent_and_does_not_grant_execution |
| Accepting a snapshot grants no further execution | same test and two-user browser journey |
| Bound operating permission, exact repeat economics and one idempotent reservation | test_scoped_execution_preserves_economics_and_idempotent_budget |
| Wrong protocol/scenario rejected before scope consumption | test_wrong_protocol_or_scenario_cannot_spend_scope_budget |
| Revocation before work and during a real replay | queued and active revocation tests |
| Expiry and changed reviewer authority invalidate operation | test_expiry_and_role_change_stop_execution |
| Superseded acceptance invalidates its old scope | test_new_snapshot_decision_supersedes_old_scope |
| Cross-organization records/sessions fail | test_organization_stores_and_sessions_are_isolated |
| Session expiry and membership changes | test_session_expiry_and_membership_change_invalidate_existing_session |
| Password/invitation recovery invalidates old sessions | test_password_change_and_invitation_reissue_invalidate_existing_sessions |
| Persistent failed-login throttle and no submitted credential echo | test_login_throttling_does_not_echo_credentials |
| Worker rejects changed financial configuration | test_changed_execution_config_fails_worker_authority |
| Owner and team store roots cannot be accidentally shared | test_no_team_store_can_share_the_local_owner_runtime_directory |
| Cross-process authority mutation serialization during maintenance | test_operator_and_server_processes_serialize_authority_mutations |
| Identity/review ledger and backup integrity | test_identity_and_review_backup_integrity |
| Standalone app contains no legacy/broker routes | test_team_only_application_exposes_no_legacy_or_broker_routes |
| Verified restore retains exact evidence and requires renewed authority | test_backup_restore_preserves_evidence_and_requires_new_authority |
| Retention preserves unrelated/unverified files | test_retention_only_removes_managed_verified_snapshots |
| Pause/checkpoint survives restore without automatic resumption | test_paused_research_survives_isolated_restore_without_autoresume |
| Real GPG encryption/decryption, controlled SFTP readback and restored identity | test_encrypted_offhost_copy_readback_and_decrypted_restore |
| Two independent browser sessions, actual workers, review/scope/revoke/export/backup and mobile | tools/team_browser.py |
| Existing financial/account/broker/candidate/forward workflows | complete tools/validate_workbench.py suite |
| Running app and new team entry reject anonymous access without creating real users/examples | tools/workbench_deployed_browser.py |

Tests are in tests/test_team.py and tests/test_team_recovery.py. Synthetic data, controlled
identities and a controlled SFTP implementation establish the behavior tested, not genuine
external-host delivery or customer acceptance. Unit crypto tests generate their own temporary
test keyring and use the installed GPG executable. No broker or AI provider credentials are used.

Final outcomes/counts belong in implementation.json and the generated validation/deployment
records. Real off-host transfer/restore, recipient ownership, public TLS operation, team
workflow acceptance and independent financial/security review remain pending.
