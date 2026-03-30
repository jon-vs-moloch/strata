import asyncio

from strata.models.adapter import ModelAdapter
from strata.schemas.execution import AgentExecutionContext, TrainerExecutionContext


def test_extract_structured_object_parses_fenced_json():
    adapter = ModelAdapter()
    parsed = adapter.extract_structured_object("```json\n{\"verdict\":\"good\",\"confidence\":0.9}\n```")
    assert parsed["verdict"] == "good"
    assert parsed["confidence"] == 0.9


def test_extract_structured_object_recovers_json_from_mixed_text():
    adapter = ModelAdapter()
    parsed = adapter.extract_structured_object(
        'Here is the result:\n{"resolution":"decompose","reasoning":"too broad","new_subtasks":[]}\nThanks.'
    )
    assert parsed["resolution"] == "decompose"
    assert parsed["reasoning"] == "too broad"


def test_extract_structured_object_parses_yaml_mapping():
    adapter = ModelAdapter()
    parsed = adapter.extract_structured_object(
        "plan_health: degraded\nrecommendation: decompose\nconfidence: 0.9\nrationale: scope too large"
    )
    assert parsed["plan_health"] == "degraded"
    assert parsed["recommendation"] == "decompose"


class CloudProvider:
    provider_id = "cloud-test"
    model_id = "cloud-model"

    async def complete(self, *_args, **_kwargs):
        raise AssertionError("Transport guard should trigger before provider.complete")


class LocalProvider:
    provider_id = "local-test"
    model_id = "local-model"

    async def complete(self, *_args, **_kwargs):
        raise AssertionError("Transport guard should trigger before provider.complete")


def test_agent_lane_rejects_cloud_provider(monkeypatch):
    adapter = ModelAdapter(context=AgentExecutionContext(run_id="agent-test"))
    monkeypatch.setattr(adapter.registry, "get_provider_for_context", lambda *args, **kwargs: CloudProvider())

    result = asyncio.run(adapter.chat([{"role": "user", "content": "ping"}]))

    assert result["status"] == "error"
    assert "Agent lane transport violation" in result["message"]


def test_trainer_lane_rejects_local_provider(monkeypatch):
    adapter = ModelAdapter(context=TrainerExecutionContext(run_id="trainer-test"))
    monkeypatch.setattr(adapter.registry, "get_provider_for_context", lambda *args, **kwargs: LocalProvider())

    result = asyncio.run(adapter.chat([{"role": "user", "content": "ping"}]))

    assert result["status"] == "error"
    assert "Trainer lane transport violation" in result["message"]
