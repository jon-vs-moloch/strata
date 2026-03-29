from types import SimpleNamespace

from strata.orchestrator.worker.routing_policy import select_model_tier


def make_task(task_id: str, risk: str = "low"):
    return SimpleNamespace(task_id=task_id, risk=risk)


def test_background_routing_defaults_to_weak():
    context = select_model_tier(make_task("task-default"))

    assert context.mode == "agent"


def test_background_routing_does_not_cross_pool_on_risk_by_default():
    context = select_model_tier(make_task("task-risky", risk="high"))

    assert context.mode == "agent"
