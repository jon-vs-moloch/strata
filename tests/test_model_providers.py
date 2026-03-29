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


def test_local_provider_adapts_cooldown_to_observed_latency_and_errors():
    provider = LocalProvider(
        model_id="demo-model",
        provider_id="lmstudio",
        endpoint_url="http://127.0.0.1:1234/v1/chat/completions",
        min_interval_ms=500,
    )
    telemetry = provider._get_telemetry_state()
    telemetry.request_count = 6
    telemetry.success_count = 4
    telemetry.error_count = 2
    telemetry.total_latency_s = 32.0

    effective = provider._effective_min_interval_ms(telemetry)

    assert effective > 500
