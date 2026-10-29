"""Inspector tools. Technology-neutral: they work on the IR the expert agents export, never on TML or Strategy
files. Independent of the agent; can move to a tool repository unchanged.

    package_inventory    inspect_package               (manifest, mapping + the experts' IR exports via A2A)
    expert_client        ask_expert, analyze_sql       (talk to the ThoughtSpot / Strategy experts)
    model_browser        list_fields, get_object       (browse either side's IR)
    lineage_tracer       trace_lineage                 (fields -> warehouse columns)
    counterpart_finder   match_fields, find_counterparts
    definition_comparer  compare_fields                (canonical proof / difference class)
    join_analyzer        get_join_graph, compare_joins, analyze_row_multiplication
    filter_inspector     compare_filters
    rule_sweep           run_rule_sweep                (deterministic checks -> leads)
    data_probe           request_data_probe            (data agent: profiles, reconciliation)
    findings_ledger      record_finding, finalize_report  (evidence rules, verdict)
"""
