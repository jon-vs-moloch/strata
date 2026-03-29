from strata.models.providers import LocalProvider


def test_provider_normalizes_nested_usage_payloads():
    provider = LocalProvider(
        model_id="demo-model",
        provider_id="demo",
        endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
    )

    normalized = provider._normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 167, "audio_tokens": None},
            "unsupported": object(),
        }
    )

    assert normalized["prompt_tokens"] == 10
    assert normalized["completion_tokens_details"] == {"reasoning_tokens": 167, "audio_tokens": None}
    assert "unsupported" not in normalized
