from strata.models.adapter import ModelAdapter


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
