"""
@module api.eval_admin
@purpose Assemble eval and experiment admin route groups.

This module is intentionally thin. The actual endpoints live in smaller route
modules so small-context models can inspect eval execution and experiment
governance separately.
"""

from __future__ import annotations
from typing import Any, Dict

from strata.api.eval_routes import register_eval_routes
from strata.api.experiment_admin import register_experiment_routes


def register_eval_admin_routes(
    app,
    *,
    get_storage,
    model_adapter,
    queue_eval_system_job,
    build_dashboard_snapshot,
    apply_experiment_promotion,
    generate_eval_candidate_from_tier,
    generate_tool_candidate_from_tier,
    eval_override_signature,
    get_provider_telemetry_snapshot,
) -> Dict[str, Any]:
    exported: Dict[str, Any] = {}
    exported.update(
        register_eval_routes(
            app,
            get_storage=get_storage,
            queue_eval_system_job=queue_eval_system_job,
            build_dashboard_snapshot=build_dashboard_snapshot,
            get_provider_telemetry_snapshot=get_provider_telemetry_snapshot,
        )
    )
    exported.update(
        register_experiment_routes(
            app,
            get_storage=get_storage,
            model_adapter=model_adapter,
            queue_eval_system_job=queue_eval_system_job,
            apply_experiment_promotion=apply_experiment_promotion,
            generate_eval_candidate_from_tier=generate_eval_candidate_from_tier,
            generate_tool_candidate_from_tier=generate_tool_candidate_from_tier,
            eval_override_signature=eval_override_signature,
        )
    )
    return exported
