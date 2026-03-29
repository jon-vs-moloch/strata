from strata.models.registry import ModelRegistry
from strata.schemas.execution import StrongExecutionContext, WeakExecutionContext


def test_registry_supports_legacy_list_pool_shape():
    registry = ModelRegistry(
        {
            "strong": [
                {
                    "provider": "test-cloud",
                    "model": "big-cloud",
                    "transport": "cloud",
                    "endpoint_url": "https://example.com/v1/chat/completions",
                }
            ]
        }
    )

    endpoint = registry.resolve_endpoint_for_context(StrongExecutionContext(run_id="legacy-shape"))

    assert endpoint.model == "big-cloud"
    assert registry.pools["strong"].allow_cloud is True
    assert registry.pools["strong"].allow_local is True


def test_registry_prefers_pool_transport_without_hardcoding_tier_transport():
    registry = ModelRegistry(
        {
            "weak": {
                "allow_cloud": True,
                "allow_local": True,
                "preferred_transport": "cloud",
                "endpoints": [
                    {
                        "provider": "local-test",
                        "model": "small-local",
                        "transport": "local",
                        "endpoint_url": "http://127.0.0.1:1234/v1/chat/completions",
                    },
                    {
                        "provider": "cloud-test",
                        "model": "cheap-cloud",
                        "transport": "cloud",
                        "endpoint_url": "https://example.com/v1/chat/completions",
                    },
                ],
            }
        }
    )

    endpoint = registry.resolve_endpoint_for_context(WeakExecutionContext(run_id="weak-cloud-ok"))

    assert endpoint.model == "cheap-cloud"
    assert endpoint.transport == "cloud"


def test_context_transport_override_still_works():
    registry = ModelRegistry(
        {
            "strong": {
                "allow_cloud": True,
                "allow_local": True,
                "preferred_transport": "cloud",
                "endpoints": [
                    {
                        "provider": "cloud-test",
                        "model": "big-cloud",
                        "transport": "cloud",
                        "endpoint_url": "https://example.com/v1/chat/completions",
                    },
                    {
                        "provider": "local-test",
                        "model": "big-local",
                        "transport": "local",
                        "endpoint_url": "http://127.0.0.1:1234/v1/chat/completions",
                    },
                ],
            }
        }
    )

    endpoint = registry.resolve_endpoint_for_context(
        StrongExecutionContext(run_id="force-local", allow_cloud=False, allow_local=True)
    )

    assert endpoint.model == "big-local"
    assert endpoint.transport == "local"
