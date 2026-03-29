from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from strata.api.experiment_runtime import (
    extract_json_object,
    generate_eval_candidate_from_tier,
    summarize_eval_variant_metrics,
)


class DummyAdapter:
    def bind_execution_context(self, context):
        self.context = context

    async def chat(self, messages, temperature=0.0):
        return {"content": "not json at all"}


def test_extract_json_object_handles_fenced_json():
    parsed = extract_json_object("```json\n{\"candidate_suffix\":\"demo\"}\n```")
    assert parsed["candidate_suffix"] == "demo"


def test_generate_eval_candidate_from_tier_falls_back_on_malformed_json():
    async def run():
        result = await generate_eval_candidate_from_tier(
            "agent",
            {"system_prompt": "base prompt", "context_files": ["README.md"]},
            lambda: DummyAdapter(),
        )
        assert result["eval_harness_config_override"]["system_prompt"] == "base prompt"
        assert result["eval_harness_config_override"]["context_files"] == ["README.md"]
        assert "malformed JSON" in result["rationale"]
        assert result["raw_proposal"]["parse_error"] == "not json at all"

    asyncio.run(run())


def test_summarize_eval_variant_metrics_groups_latest_and_series():
    now = datetime.utcnow()

    class Row:
        def __init__(self, metric_name, value, seconds, details):
            self.metric_name = metric_name
            self.value = value
            self.timestamp = now + timedelta(seconds=seconds)
            self.details = details
            self.model_id = details.get("variant_id")

    rows = [
        Row("eval_sample_tick_accuracy", 0.4, 1, {"variant_id": "weak_raw_model", "mode": "agent", "profile": "raw_model", "suite_name": "mmlu_mini_v1", "include_context": False}),
        Row("eval_sample_tick_accuracy", 0.6, 2, {"variant_id": "weak_raw_model", "mode": "agent", "profile": "raw_model", "suite_name": "mmlu_mini_v1", "include_context": False}),
        Row("eval_sample_tick_latency_s", 12.0, 2, {"variant_id": "weak_raw_model", "mode": "agent", "profile": "raw_model", "suite_name": "mmlu_mini_v1", "include_context": False}),
        Row("eval_sample_tick_accuracy", 0.8, 3, {"variant_id": "weak_harness_no_capes", "mode": "agent", "profile": "harness_no_capes", "suite_name": "mmlu_mini_v1", "include_context": False}),
    ]

    summary = summarize_eval_variant_metrics(rows, series_limit=5)

    assert summary["variant_count"] == 2
    raw = next(item for item in summary["variants"] if item["variant_id"] == "weak_raw_model")
    assert raw["latest_accuracy"] == 0.6
    assert raw["metrics"]["eval_sample_tick_accuracy"]["delta"] == 0.2
    assert raw["latest_latency_s"] == 12.0
