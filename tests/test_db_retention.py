from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.experimental.experiment_runner import ExperimentResult, ExperimentRunner, normalize_experiment_report
from strata.orchestrator.worker.telemetry import build_telemetry_snapshot
from strata.storage.models import (
    Base,
    AttemptModel,
    AttemptOutcome,
    MetricModel,
    ParameterModel,
    TaskModel,
    TaskState,
    TaskType,
)
from strata.storage.retention import (
    DB_RETENTION_POLICY_KEY,
    DEFAULT_DB_RETENTION_POLICY,
    run_retention_maintenance,
)
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def set_policy(storage, **overrides):
    policy = dict(DEFAULT_DB_RETENTION_POLICY)
    policy.update(overrides)
    storage.parameters.set_parameter(
        DB_RETENTION_POLICY_KEY,
        policy,
        description="test policy",
    )
    storage.commit()


def test_message_retention_archives_old_session_history():
    storage = make_storage()
    set_policy(storage, message_keep_per_session=5)

    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    for index in range(8):
        msg = storage.messages.create(
            role="user" if index % 2 == 0 else "assistant",
            content=f"message {index}",
            session_id="chat-a",
        )
        msg.created_at = (base + timedelta(minutes=index)).replace(tzinfo=None)
    storage.commit()

    summary = run_retention_maintenance(storage, force=True)

    assert summary["messages"]["archived_messages"] == 3
    remaining = storage.messages.get_all(session_id="chat-a")
    assert len(remaining) == 5
    archive = storage.parameters.peek_parameter("message_archive:chat-a", default_value={})
    assert archive["epochs"][-1]["archived_count"] == 3


def test_metric_retention_preserves_rollup_signal_in_snapshot():
    storage = make_storage()
    set_policy(storage, metric_raw_keep_count=1, metric_raw_keep_days=1)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).replace(tzinfo=None)
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    storage.session.add_all(
        [
            MetricModel(
                timestamp=old_ts,
                metric_name="benchmark_harness_score",
                value=8.0,
                run_mode="weak_eval",
                execution_context="weak",
                candidate_change_id="candidate-old",
            ),
            MetricModel(
                timestamp=recent_ts,
                metric_name="benchmark_harness_score",
                value=9.0,
                run_mode="weak_eval",
                execution_context="weak",
                candidate_change_id="candidate-new",
            ),
        ]
    )
    storage.commit()

    summary = run_retention_maintenance(storage, force=True)
    snapshot = build_telemetry_snapshot(storage, limit=5)

    assert summary["metrics"]["archived_metrics"] == 1
    assert snapshot["overview"]["weak_eval_runs"] == 2
    assert snapshot["overview"]["unique_experiments"] == 2
    assert snapshot["archived_history"]["archived_metrics"] == 1
    assert snapshot["rollups"][0]["metric_name"] == "benchmark_harness_score"
    assert snapshot["rollups"][0]["count"] == 2


def test_attempt_retention_keeps_recent_attempts_and_archives_older_counts():
    storage = make_storage()
    set_policy(storage, terminal_attempt_keep_per_task=2, terminal_attempt_compaction_days=1)

    task = storage.tasks.create(
        title="Old terminal task",
        description="done",
        state=TaskState.COMPLETE,
        type=TaskType.RESEARCH,
    )
    old_time = (datetime.now(timezone.utc) - timedelta(days=3)).replace(tzinfo=None)
    task.created_at = old_time
    task.updated_at = old_time
    for outcome in (
        AttemptOutcome.SUCCEEDED,
        AttemptOutcome.FAILED,
        AttemptOutcome.FAILED,
        AttemptOutcome.SUCCEEDED,
    ):
        attempt = storage.attempts.create(task_id=task.task_id)
        attempt.started_at = old_time
        attempt.ended_at = old_time
        attempt.outcome = outcome
    storage.commit()

    summary = run_retention_maintenance(storage, force=True)
    storage.session.expire_all()
    reloaded_task = storage.tasks.get_by_id(task.task_id)
    snapshot = build_telemetry_snapshot(storage, limit=5)

    remaining_attempts = storage.attempts.get_by_task_id(task.task_id)
    assert summary["attempts"]["archived_attempts"] == 2
    assert len(remaining_attempts) == 2
    assert reloaded_task.constraints["archived_attempt_summary"]["archived_count"] == 2
    assert snapshot["overview"]["total_attempts"] == 4
    assert snapshot["overview"]["failed_attempts"] == 2


def test_experiment_report_retention_compacts_older_unpromoted_payloads():
    storage = make_storage()
    set_policy(storage, experiment_keep_full_reports=1, experiment_keep_promoted_full_reports=1)

    promoted_report = {
        "candidate_change_id": "candidate-promoted",
        "evaluation_kind": "full_eval",
        "recommendation": "promote",
        "benchmark_reports": [{"sample": 1}],
        "structured_reports": [{"sample": 1}],
        "deltas": {"benchmark_harness_score": 1.0},
    }
    stale_report = {
        "candidate_change_id": "candidate-stale",
        "evaluation_kind": "full_eval",
        "recommendation": "reject",
        "benchmark_reports": [{"sample": 1}, {"sample": 2}],
        "structured_reports": [{"sample": 3}],
        "baseline_metrics": {"x": 1},
        "candidate_metrics": {"x": 2},
        "deltas": {"benchmark_harness_score": -1.0},
        "eval_harness_config_override": {"system_prompt": "x"},
    }
    storage.parameters.set_parameter("experiment_report:candidate-promoted", promoted_report, description="report")
    storage.parameters.set_parameter("experiment_report:candidate-stale", stale_report, description="report")
    storage.parameters.set_parameter(
        "promoted_eval_candidates",
        {
            "current": "candidate-promoted",
            "history": [{"candidate_change_id": "candidate-promoted"}],
        },
        description="promoted",
    )
    storage.commit()

    rows = (
        storage.session.query(ParameterModel)
        .filter(ParameterModel.key.like("experiment_report:%"))
        .order_by(ParameterModel.key.asc())
        .all()
    )
    for idx, row in enumerate(rows):
        row.updated_at = (datetime.now(timezone.utc) - timedelta(days=idx + 1)).replace(tzinfo=None)
    storage.commit()

    summary = run_retention_maintenance(storage, force=True)
    stale_row = storage.session.query(ParameterModel).filter_by(key="experiment_report:candidate-stale").first()
    stale_payload = normalize_experiment_report(stale_row.value)

    assert summary["experiment_reports"]["compacted_reports"] == 1
    assert stale_payload["payload_compacted"] is True
    assert stale_payload["summary"]["benchmark_report_count"] == 2
    assert "baseline_metrics" not in stale_payload


def test_active_task_linked_report_stays_hot_and_task_gets_back_reference():
    storage = make_storage()
    set_policy(storage, experiment_keep_full_reports=0, experiment_keep_promoted_full_reports=0)

    active_task = storage.tasks.create(
        title="Active bootstrap task",
        description="still working",
        state=TaskState.PENDING,
        type=TaskType.RESEARCH,
    )
    terminal_task = storage.tasks.create(
        title="Old terminal task",
        description="done",
        state=TaskState.COMPLETE,
        type=TaskType.RESEARCH,
    )
    runner = ExperimentRunner(storage, model_adapter=None)
    result = ExperimentResult(
        success=True,
        valid=True,
        candidate_change_id="candidate-linked-active",
        baseline_metrics={},
        candidate_metrics={},
        deltas={"benchmark_harness_score": 1.0},
        recommendation="promote",
        notes="linked report",
    )
    runner._persist_experiment_report(
        result,
        baseline_change_id="baseline",
        evaluation_kind="full_eval",
        run_count=1,
        source_task_id=active_task.task_id,
        associated_task_ids=[terminal_task.task_id],
    )

    row = storage.session.query(ParameterModel).filter_by(key="experiment_report:candidate-linked-active").first()
    row.updated_at = (datetime.now(timezone.utc) - timedelta(days=5)).replace(tzinfo=None)
    storage.commit()

    summary = run_retention_maintenance(storage, force=True)
    storage.session.expire_all()
    report = normalize_experiment_report(
        storage.session.query(ParameterModel).filter_by(key="experiment_report:candidate-linked-active").first().value
    )
    active_task_reloaded = storage.tasks.get_by_id(active_task.task_id)

    assert summary["experiment_reports"]["compacted_reports"] == 0
    assert report.get("payload_compacted") is not True
    assert report["task_associations"]["source_task_id"] == active_task.task_id
    assert active_task_reloaded.constraints["associated_reports"][0]["candidate_change_id"] == "candidate-linked-active"
