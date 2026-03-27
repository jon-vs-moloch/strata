from types import SimpleNamespace

from strata.experimental.experiment_runner import ExperimentRunner
from strata.experimental.variants import (
    DEFAULT_RATING,
    ensure_variant,
    get_variant,
    get_variant_rating_snapshot,
    record_variant_matchup,
    variant_signature,
)


class DummyParameters:
    def __init__(self):
        self.values = {}

    def peek_parameter(self, key, default_value=None):
        wrapped = self.values.get(key)
        if wrapped is None:
            return default_value
        if isinstance(wrapped, dict) and "current" in wrapped:
            return wrapped.get("current", default_value)
        return wrapped

    def set_parameter(self, key, value, description=""):
        self.values[key] = {"current": value, "history": [], "description": description}


class DummyStorage:
    def __init__(self):
        self.parameters = DummyParameters()


def test_variant_signature_is_stable_across_key_order():
    left = variant_signature("eval_harness_bundle", {"system_prompt": "a", "context_files": ["x"]})
    right = variant_signature("eval_harness_bundle", {"context_files": ["x"], "system_prompt": "a"})
    assert left == right


def test_ensure_variant_deduplicates_and_tracks_usage():
    storage = DummyStorage()
    first = ensure_variant(
        storage,
        kind="eval_harness_bundle",
        payload={"system_prompt": "prompt", "context_files": ["a.md"]},
        label="candidate-a",
        family="eval_harness",
    )
    second = ensure_variant(
        storage,
        kind="eval_harness_bundle",
        payload={"context_files": ["a.md"], "system_prompt": "prompt"},
        label="candidate-b",
        family="eval_harness",
    )
    assert first["variant_id"] == second["variant_id"]
    persisted = get_variant(storage, first["variant_id"])
    assert persisted["use_count"] == 2


def test_record_variant_matchup_updates_domain_scoped_ratings():
    storage = DummyStorage()
    left = ensure_variant(storage, kind="eval_harness_bundle", payload={"system_prompt": "a"}, label="left")
    right = ensure_variant(storage, kind="eval_harness_bundle", payload={"system_prompt": "b"}, label="right")

    snapshot = record_variant_matchup(
        storage,
        domain="eval_harness_full_eval:bootstrap_mcq_v1",
        left_variant_id=left["variant_id"],
        right_variant_id=right["variant_id"],
        left_score=1.0,
        context={"candidate_change_id": "left"},
    )

    assert snapshot["left"]["rating"] > DEFAULT_RATING
    assert snapshot["right"]["rating"] < DEFAULT_RATING
    rating_snapshot = get_variant_rating_snapshot(storage)
    assert rating_snapshot["ratings"]["by_domain"]["eval_harness_full_eval:bootstrap_mcq_v1"][left["variant_id"]]["matches"] == 1
    assert rating_snapshot["recent_matchups"][-1]["context"]["candidate_change_id"] == "left"


def test_ab_run_evidence_turns_repeated_eval_runs_into_aggregatable_matchups():
    storage = DummyStorage()
    runner = ExperimentRunner(storage, model_adapter=None)
    baseline = ensure_variant(storage, kind="eval_harness_bundle", payload={"system_prompt": "baseline"}, label="baseline")
    candidate = ensure_variant(storage, kind="eval_harness_bundle", payload={"system_prompt": "candidate"}, label="candidate")

    summary = runner._record_ab_run_evidence(
        domain="eval_harness_full_eval:bootstrap_mcq_v1",
        variant_assignment={
            "candidate_variant_id": candidate["variant_id"],
            "baseline_variant_id": baseline["variant_id"],
        },
        candidate_change_id="candidate_change",
        benchmark_reports=[
            {"run_label": "bench-1", "prompt_count": 4, "harness_wins": 3, "ties": 0},
            {"run_label": "bench-2", "prompt_count": 4, "harness_wins": 2, "ties": 2},
        ],
        structured_reports=[
            {"run_label": "struct-1", "suite_name": "bootstrap_mcq_v1", "baseline_accuracy": 0.25, "harness_accuracy": 0.5},
        ],
    )

    assert summary["matchup_count"] == 3
    snapshot = get_variant_rating_snapshot(storage)
    assert snapshot["ratings"]["by_domain"]["eval_harness_full_eval:bootstrap_mcq_v1:benchmark_ab"][candidate["variant_id"]]["matches"] == 2
    assert snapshot["ratings"]["by_domain"]["eval_harness_full_eval:bootstrap_mcq_v1:structured_ab"][candidate["variant_id"]]["matches"] == 1
    assert snapshot["recent_matchups"][-1]["context"]["candidate_change_id"] == "candidate_change"
