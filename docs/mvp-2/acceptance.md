# MVP-2 acceptance inventory

Controlled inputs validate implementation behavior; they do not authenticate market data.

| Boundary | Evidence |
|---|---|
| Frozen definition, dataset, build, policy and schedule | test_protocol_freezes_exact_semantics_and_changed_policy_has_new_identity |
| Unqualified data requires acknowledgment | test_synthetic_or_incomplete_data_requires_explicit_acknowledgment |
| First receipt/availability clocks causal | test_quote_receipt_contract_retains_both_clocks_and_quarantines_time_travel |
| Receipt cutoff excludes delayed observations | test_receipt_after_cutoff_cannot_change_a_fill |
| Timers do not invent prices/liquidity | test_scheduled_timer_uses_prior_quotes_without_inventing_market_observations |
| Hedge/short ordering and independently calculated ₹312 after-spread P&L | test_exact_basket_fill_order_and_independent_after_cost_accounting |
| Missing short-exit liquidity retains its hedge | test_missing_exit_liquidity_keeps_hedge_until_short_is_closed |
| Incomplete entries cancel/unwind without short exposure | test_incomplete_basket_entry_expires_and_unwinds_without_a_short |
| Identical economic inputs, distinct durable attempts | test_repeat_has_identical_economic_hash_but_distinct_durable_attempt |
| Predeclared cost/latency stress | test_cost_stress_is_predeclared_and_economically_adverse |
| Lost dispatch response recovers one durable attempt | test_dispatch_interruption_recovers_the_same_reserved_attempt |
| Cancelled/failed trials consume budget and stay in packets | cancellation, failure and budget cases |
| Cancellation after fills preserves committed traces/costs | test_cancel_after_fills_preserves_committed_costs_and_partial_trace |
| Packet hashes, optional traces, expected identities and tamper detection | packet integrity cases and browser uploads |
| Event-index corruption detected even with recalculated envelope | test_event_index_structure_checked_even_with_recomputed_envelope |
| No quote-bearing fill/position records in default exports | test_summary_export_omits_quote_positions_and_trace_requires_ack |
| Changed build blocks new runs and makes old packets historical | test_changed_build_is_historical_and_cannot_start_more_trials |
| Backup record/request/chain checks | test_backup_checks_records_and_detects_event_chain_mutation |
| Local request boundary, redaction, isolated financial state | test_api_boundary_explicit_example_and_no_financial_mutation |
| Full browser workflow using actual process workers; desktop/mobile | tools/contract_research_browser.py |
| Existing 51 templates and old views remain usable | both contract browser suites and complete workspace validator |
| Deployment preserves inactive broker authority/account/cache | tools/deploy_workbench.py and tools/workbench_deployed_browser.py |

Focused tests are in tests/test_contract_research.py. Existing financial, research,
candidate, forward, broker-pilot and workbench suites remain required regressions.
Use implementation.json and linked generated artifacts for actual counts/outcomes.

External acceptance still requires genuine source rights/provenance, customer workflow
evidence, independently reviewed financial cases, prospective observation and later
authenticated team controls. No generated example satisfies those dependencies.
