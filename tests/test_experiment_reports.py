import asyncio
from types import SimpleNamespace

import pytest

from strata.api import main as api_main
from strata.api import experiment_runtime as experiment_runtime
from strata.experimental.experiment_runner import (
    ExperimentRunner,
    normalize_experiment_report,
    report_has_weak_gain,
)
from strata.experimental.diagnostics import build_eval_trace_summary


class FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._limit = None

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, limit):
        self._limit = limit
        return self

    def all(self):
        rows = self._rows
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def filter_by(self, **kwargs):
        key = kwargs.get("key")
        rows = [row for row in self._rows if getattr(row, "key", None) == key]
        return FakeQuery(rows)

    def first(self):
        rows = self.all()
        return rows[0] if rows else None


class FakeSession:
    def __init__(self, rows):
        self._rows = list(rows)

    def query(self, _model):
        return FakeQuery(self._rows)


class FakeParameters:
    def __init__(self, values):
        self._values = dict(values)

    def peek_parameter(self, key, default_value=None):
        return self._values.get(key, default_value)


class FakeStorage:
    def __init__(self, rows, parameter_values):
        self.session = FakeSession(rows)
        self.parameters = FakeParameters(parameter_values)


def make_row(key, value):
    return SimpleNamespace(key=key, value=value, updated_at=1)


def make_report(
    candidate_change_id,
    *,
    proposer_tier="weak",
    recommendation="promote",
    benchmark_delta=0.0,
    structured_delta=0.0,
):
    return {
        "candidate_change_id": candidate_change_id,
        "evaluation_kind": "full_eval",
        "recommendation": recommendation,
        "recorded_at": "2026-03-26T00:00:00+00:00",
        "proposal_metadata": {"proposer_tier": proposer_tier},
        "promotion_readiness": {"ready_for_promotion": recommendation == "promote"},
        "diagnostic_review": {
            "status": "ok",
            "primary_failure_mode": "tool_avoidance",
            "recommended_fix": "Require a repo inspection step before declaring missing context.",
            "summary": "The weak model is not inspecting the repo before giving up.",
        },
        "deltas": {
            "benchmark_harness_score": benchmark_delta,
            "structured_eval_harness_accuracy": structured_delta,
        },
    }


def test_normalize_experiment_report_accepts_raw_and_wrapped_payloads():
    raw = make_report("raw_candidate")
    wrapped = {"current": make_report("wrapped_candidate"), "history": []}

    assert normalize_experiment_report(raw)["candidate_change_id"] == "raw_candidate"
    assert normalize_experiment_report(wrapped)["candidate_change_id"] == "wrapped_candidate"
    assert normalize_experiment_report({"history": []}) == {}
    assert normalize_experiment_report("not-a-dict") == {}


def test_get_persisted_experiment_report_handles_raw_and_wrapped_rows():
    rows = [
        make_row("experiment_report:raw_candidate", make_report("raw_candidate")),
        make_row(
            "experiment_report:wrapped_candidate",
            {"current": make_report("wrapped_candidate"), "history": []},
        ),
    ]
    storage = FakeStorage(rows, {})
    runner = ExperimentRunner(storage, model_adapter=None)

    assert runner.get_persisted_experiment_report("raw_candidate")["candidate_change_id"] == "raw_candidate"
    assert runner.get_persisted_experiment_report("wrapped_candidate")["candidate_change_id"] == "wrapped_candidate"
    assert runner.get_persisted_experiment_report("missing_candidate") is None


def test_build_dashboard_snapshot_counts_promotions_and_detects_ignition(monkeypatch):
    weak_report = make_report("weak_candidate", proposer_tier="weak", benchmark_delta=1.0)
    strong_report = make_report("strong_candidate", proposer_tier="strong", benchmark_delta=0.5)
    rows = [
        make_row("experiment_report:weak_candidate", {"current": weak_report, "history": []}),
        make_row("experiment_report:strong_candidate", strong_report),
    ]
    storage = FakeStorage(
        rows,
        {
            "promoted_eval_candidates": {
                "current": "strong_candidate",
                "history": [
                    {"candidate_change_id": "weak_candidate"},
                    {"candidate_change_id": "strong_candidate"},
                ],
            }
        },
    )

    monkeypatch.setattr(
        api_main,
        "build_telemetry_snapshot",
        lambda _storage, limit=10: {
            "generated_at": "2026-03-26T00:00:00",
            "overview": {"weak_eval_runs": 2, "unique_experiments": 2},
            "recent_metrics": [
                {"metric_name": "task_failure", "task_type": "RESEARCH"},
                {"metric_name": "task_failure", "task_type": "RESEARCH"},
            ],
        },
    )
    monkeypatch.setattr(
        api_main,
        "get_provider_telemetry_snapshot",
        lambda: {"weak-provider": {"request_count": 1}},
    )
    monkeypatch.setattr(
        experiment_runtime,
        "list_spec_proposals",
        lambda _storage, limit=5: [
            {
                "proposal_id": "spec_1",
                "scope": "project",
                "status": "pending_review",
                "summary": "measure outcomes",
            }
        ],
    )
    monkeypatch.setattr(
        api_main,
        "get_context_load_telemetry",
        lambda _storage: {
            "warnings": [{"warning": "context_load_large_artifact"}],
            "recent": [{"artifact_type": "spec"}],
            "stats": {"artifacts": {"spec:project_spec": {"artifact_type": "spec", "identifier": "project_spec", "load_count": 3, "total_estimated_tokens": 200, "max_estimated_tokens": 72}}},
            "file_scan": {"largest_files": [{"path": "strata/specs/bootstrap.py"}]},
        },
    )

    snapshot = api_main._build_dashboard_snapshot(storage, limit=10)

    assert snapshot["promotion_counts"]["weak"] == 1
    assert snapshot["promotion_counts"]["strong"] == 1
    assert snapshot["ignition"]["detected"] is True
    assert snapshot["ignition"]["candidate_change_id"] == "weak_candidate"
    assert snapshot["reports"][0]["candidate_change_id"] == "weak_candidate"
    assert snapshot["context_pressure"]["warning_count"] == 1
    assert snapshot["spec_governance"]["pending_count"] == 1


@pytest.mark.parametrize(
    ("current_candidate", "report_rows", "expected_detected"),
    [
        (None, [], False),
        ("missing_report", [], False),
        ("weak_candidate", [make_row("experiment_report:weak_candidate", make_report("weak_candidate", benchmark_delta=0.0))], False),
        ("weak_candidate", [make_row("experiment_report:weak_candidate", make_report("weak_candidate", benchmark_delta=1.0))], True),
    ],
)
def test_secondary_ignition_status_is_stable(current_candidate, report_rows, expected_detected):
    storage = FakeStorage(
        report_rows,
        {
            "promoted_eval_candidates": {
                "current": current_candidate,
                "history": [],
            }
        },
    )

    result = asyncio.run(api_main.get_secondary_ignition_status(storage=storage))
    assert result["detected"] is expected_detected
    assert "reason" in result


def test_experiment_history_returns_real_candidate_ids():
    rows = [
        make_row("experiment_report:raw_candidate", make_report("raw_candidate", proposer_tier="weak")),
        make_row(
            "experiment_report:wrapped_candidate",
            {"current": make_report("wrapped_candidate", proposer_tier="strong"), "history": []},
        ),
    ]
    storage = FakeStorage(
        rows,
        {"promoted_eval_candidates": {"current": "wrapped_candidate", "history": []}},
    )

    result = asyncio.run(api_main.get_experiment_history(limit=10, storage=storage))
    candidate_ids = [report["candidate_change_id"] for report in result["reports"]]
    assert candidate_ids == ["raw_candidate", "wrapped_candidate"]
    assert result["reports"][0]["promotion_readiness"]["ready_for_promotion"] is True
    assert result["reports"][0]["diagnostic_review"]["primary_failure_mode"] == "tool_avoidance"
    assert result["current_promoted_candidate"] == "wrapped_candidate"


def test_report_has_weak_gain_handles_missing_and_positive_deltas():
    assert report_has_weak_gain({}) is False
    assert report_has_weak_gain({"deltas": {"benchmark_harness_score": 0.1}}) is True


def test_build_eval_trace_summary_preserves_sample_evidence():
    summary = build_eval_trace_summary(
        candidate_change_id="candidate_a",
        baseline_change_id="baseline",
        benchmark_reports=[
            {
                "run_label": "bench-1",
                "baseline_wins": 1,
                "harness_wins": 2,
                "ties": 0,
                "average_baseline_score": 5.0,
                "average_harness_score": 7.0,
                "samples": [
                    {
                        "prompt_id": "repo_orientation",
                        "winner": "baseline",
                        "baseline_score": 7,
                        "harness_score": 4,
                        "baseline_latency_s": 1.0,
                        "harness_latency_s": 2.0,
                        "rationale": "Harness refused instead of inspecting the repo.",
                        "baseline_response": "Direct answer",
                        "harness_response": "I need the codebase first",
                    }
                ],
            }
        ],
        structured_reports=[
            {
                "run_label": "struct-1",
                "suite_name": "bootstrap_mcq_v1",
                "baseline_accuracy": 0.5,
                "harness_accuracy": 0.75,
                "baseline_avg_latency_s": 1.1,
                "harness_avg_latency_s": 2.2,
                "samples": [
                    {
                        "case_id": "secondary_ignition_condition",
                        "baseline_correct": False,
                        "harness_correct": True,
                        "baseline_latency_s": 1.0,
                        "harness_latency_s": 2.0,
                        "baseline_response": "Wrong",
                        "harness_response": "Right",
                    }
                ],
            }
        ],
        suite_name="bootstrap_mcq_v1",
    )

    assert summary["candidate_change_id"] == "candidate_a"
    assert summary["benchmark_samples"][0]["winner"] == "baseline"
    assert "inspect" in summary["benchmark_samples"][0]["rationale"].lower()
    assert summary["structured_samples"][0]["case_id"] == "secondary_ignition_condition"
