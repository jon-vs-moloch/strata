from strata.models.registry import ModelRegistry
from strata.schemas.execution import TrainerExecutionContext, AgentExecutionContext, RemoteAgentExecutionContext


def test_registry_supports_legacy_list_pool_shape():
    registry = ModelRegistry(
        {
            "trainer": [
                {
                    "provider": "test-cloud",
                    "model": "big-cloud",
                    "transport": "cloud",
                    "endpoint_url": "https://example.com/v1/chat/completions",
                }
            ]
        }
    )

    endpoint = registry.resolve_endpoint_for_context(TrainerExecutionContext(run_id="legacy-shape"))

    assert endpoint.model == "big-cloud"
    assert registry.pools["trainer"].allow_cloud is True
    assert registry.pools["trainer"].allow_local is True


def test_registry_prefers_pool_transport_without_hardcoding_tier_transport():
    registry = ModelRegistry(
        {
            "agent": {
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

    endpoint = registry.resolve_endpoint_for_context(AgentExecutionContext(run_id="weak-cloud-ok"))

    assert endpoint.model == "cheap-cloud"
    assert endpoint.transport == "cloud"


def test_context_transport_override_still_works():
    registry = ModelRegistry(
        {
            "trainer": {
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
        TrainerExecutionContext(run_id="force-local", allow_cloud=False, allow_local=True)
    )

    assert endpoint.model == "big-local"
    assert endpoint.transport == "local"


def test_registry_maps_legacy_agent_pool_to_local_agent_profile():
    registry = ModelRegistry(
        {
            "agent": {
                "allow_cloud": False,
                "allow_local": True,
                "preferred_transport": "local",
                "endpoints": [
                    {
                        "provider": "local-test",
                        "model": "small-local",
                        "transport": "local",
                        "endpoint_url": "http://127.0.0.1:1234/v1/chat/completions",
                    }
                ],
            }
        }
    )

    endpoint = registry.resolve_endpoint_for_context(AgentExecutionContext(run_id="legacy-agent-alias"))

    assert "local_agent" in registry.pools
    assert "agent" not in registry.pools
    assert endpoint.model == "small-local"


def test_remote_agent_profile_resolves_its_own_pool():
    registry = ModelRegistry(
        {
            "remote_agent": {
                "allow_cloud": True,
                "allow_local": False,
                "preferred_transport": "cloud",
                "endpoints": [
                    {
                        "provider": "cloud-test",
                        "model": "worker-cloud",
                        "transport": "cloud",
                        "endpoint_url": "https://example.com/v1/chat/completions",
                    }
                ],
            }
        }
    )

    endpoint = registry.resolve_endpoint_for_context(RemoteAgentExecutionContext(run_id="remote-agent"))

    assert endpoint.model == "worker-cloud"
    assert endpoint.transport == "cloud"
