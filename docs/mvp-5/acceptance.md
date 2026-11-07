# MVP-5 acceptance inventory

Use implementation.json and the linked generated artifacts for measured outcomes.
Controlled evidence is not an external customer acceptance record.

| Boundary | Executable evidence |
|---|---|
| Exact current packet/cycle binding and server elapsed duration | test_customer_journey_records_exact_packet_and_measured_wall_time |
| Idempotent retries after state changes and changed-input rejection | test_request_retries_are_idempotent_after_state_changes |
| Contributor review separation and duplicate packet rejection | test_self_review_timer_and_duplicate_packet_fail |
| Payment/refund/cost integer-paise reconciliation and balance checks | test_commercial_money_is_paise_and_partial_payments_refunds_reconcile |
| Administrator-only commercial mutation and read/export redaction | test_finance_is_admin_only_and_redacted_from_other_roles |
| Both linked invoice periods paid before renewal counts | test_paid_renewal_requires_both_invoice_periods_paid |
| Controlled exercises excluded from traction and money | test_controlled_exercises_never_become_customer_metrics |
| Future precommitment and no execution grant | test_prospective_plan_precommits_future_rules_without_execution_authority |
| Actual elapsed end, chronological records and independent discrepancy assessment | test_observation_elapsed_dates_end_and_independent_corrections |
| Material changes end the frozen observation plan | test_material_changes_end_frozen_observation_plan |
| Superseded independent acceptance stops further observation | test_superseded_review_and_changed_reviewer_stop_observation |
| Organization isolation | test_cross_organization_records_fail_without_affecting_owner |
| Verified restore preserves evidence and invalidates prior plans | test_backup_restore_keeps_customer_evidence_and_invalidates_old_plan |
| Record-chain tampering fails closed | test_customer_tampering_fails_closed |
| Cohort hashes, duplicate organizations and role scope | test_cohort_counts_each_organization_once_and_rejects_tampering |
| Anonymous denial, input redaction and exact download hash | test_customer_api_authentication_redaction_and_report_download |
| Unique commercial reference, no future money event and invoice void boundaries | test_future_finance_duplicate_reference_and_void_boundaries |
| Cross-engagement invoice denial and fractional-paise rejection | test_cross_engagement_invoice_and_fractional_paise_are_rejected |
| Late history cannot manufacture four-week activity | test_late_recording_cannot_manufacture_four_week_activity |
| Rehashed organization counter inflation rejected | test_cohort_rejects_rehashed_counter_inflation |
| Zero elapsed time cannot claim measured savings | test_zero_elapsed_review_cannot_be_claimed_as_measured_saving |
| Effort and distinct-organization integration demand | test_effort_and_repeated_integration_demand_are_counted_without_duplicate_organizations |
| Repeat review rounds add to the paired cycle duration | test_repeat_review_rounds_accumulate_instead_of_inflating_savings |
| Real-clock CLI aggregation excludes controlled receipts | test_actual_clock_offline_cohort_cli_excludes_controlled_receipts |
| Whitespace is not evidence | test_whitespace_cannot_satisfy_customer_evidence_reference |
| Audited entry correction without inventing a refund | test_entry_corrections_are_audited_and_do_not_invent_refunds |
| Future seed observations cannot be exposed before the plan | test_prospective_planning_rejects_exposed_future_seed_observations |
| Changed engine builds keep prior plans historical | test_changed_build_keeps_prior_plan_historical |
| Two real browser sessions, review handoff, observation/correction, finance, report and mobile | tools/customer_browser.py |
| Existing broker/account/research/candidate/forward/team behavior | complete tools/validate_workbench.py release |

Remaining external exit criteria: genuine repeated customer work; actual financial/data
acceptance; permitted data/customer deployment; real off-host recovery; payment and renewal
verification; elapsed four-week usage; and measured operating cost/support burden. Neither
an authored exercise nor a report reaching an operator-reported target independently passes
those criteria.
