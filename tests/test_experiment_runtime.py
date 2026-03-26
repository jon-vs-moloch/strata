from __future__ import annotations

import asyncio

from strata.api.experiment_runtime import extract_json_object, generate_eval_candidate_from_tier


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
            "weak",
            {"system_prompt": "base prompt", "context_files": ["README.md"]},
            lambda: DummyAdapter(),
        )
        assert result["eval_harness_config_override"]["system_prompt"] == "base prompt"
        assert result["eval_harness_config_override"]["context_files"] == ["README.md"]
        assert "malformed JSON" in result["rationale"]
        assert result["raw_proposal"]["parse_error"] == "not json at all"

    asyncio.run(run())
