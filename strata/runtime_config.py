"""
@module runtime_config
@purpose Shared persisted runtime settings for API and worker processes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


GLOBAL_SETTINGS: Dict[str, Any] = {
    "max_sync_tool_iterations": 3,
    "automatic_task_generation": True,
    "testing_mode": False,
    "replay_pending_tasks_on_startup": True,
    "heavy_reflection_mode": False,
    "inference_throttle_policy": {
        "throttle_mode": "hard",
        "operator_comfort": {
            "profile": "quiet",
            "ambiguity_bias": "prefer_quiet",
            "allow_annoying_if_explicit": False,
            "context": {
                "machine_in_use": True,
                "room_occupied": True,
                "ambient_noise_masking": False,
            },
        },
    },
}
SETTINGS_PARAMETER_KEY = "orchestrator_global_settings"
SETTINGS_PARAMETER_DESCRIPTION = (
    "Persisted API/orchestrator settings shared between the UI and the worker startup path."
)


def normalized_settings(
    payload: Optional[Dict[str, Any]] = None,
    *,
    base_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = dict(base_settings or GLOBAL_SETTINGS)
    normalized = dict(base)
    if payload:
        normalized.update(payload)
        current_policy = dict(base.get("inference_throttle_policy") or {})
        incoming_policy = dict(payload.get("inference_throttle_policy") or {})
        merged_policy = dict(current_policy)
        merged_policy.update({k: v for k, v in incoming_policy.items() if k != "operator_comfort"})
        current_comfort = dict(current_policy.get("operator_comfort") or {})
        incoming_comfort = dict(incoming_policy.get("operator_comfort") or {})
        merged_comfort = dict(current_comfort)
        merged_comfort.update({k: v for k, v in incoming_comfort.items() if k != "context"})
        current_context = dict(current_comfort.get("context") or {})
        incoming_context = dict(incoming_comfort.get("context") or {})
        merged_context = dict(current_context)
        merged_context.update(incoming_context)
        merged_comfort["context"] = merged_context
        merged_policy["operator_comfort"] = merged_comfort
        normalized["inference_throttle_policy"] = merged_policy
    return normalized

