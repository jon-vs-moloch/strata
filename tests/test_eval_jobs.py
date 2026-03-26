import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.api import main as api_main
from strata.eval.job_runner import run_eval_job_task
from strata.storage.models import Base, TaskState, TaskType
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_queue_benchmark_job_endpoint_creates_system_task(monkeypatch):
    storage = make_storage()
    enqueued = []

    async def fake_enqueue(task_id: str):
        enqueued.append(task_id)

    monkeypatch.setattr(api_main._worker, "enqueue", fake_enqueue)

    result = asyncio.run(
        api_main.run_benchmark_suite(
            payload={"queue": True, "candidate_change_id": "queued_candidate", "session_id": "bench-session"},
            storage=storage,
        )
    )

    task = storage.tasks.get_by_id(result["task_id"])
    assert result["status"] == "queued"
    assert result["kind"] == "benchmark"
    assert task is not None
    assert task.type == TaskType.JUDGE
    assert task.state == TaskState.PENDING
    assert task.constraints["system_job"]["kind"] == "benchmark"
    assert enqueued == [task.task_id]


def test_queue_benchmark_job_endpoint_dedupes_active_job(monkeypatch):
    storage = make_storage()
    existing = storage.tasks.create(
        title="Queued benchmark",
        description="run benchmark",
        state=TaskState.PENDING,
        type=TaskType.JUDGE,
        constraints={
            "system_job": {
                "kind": "benchmark",
                "payload": {"candidate_change_id": "queued_candidate"},
            }
        },
    )
    storage.commit()

    async def fake_enqueue(_task_id: str):
        raise AssertionError("should not enqueue duplicate task")

    monkeypatch.setattr(api_main._worker, "enqueue", fake_enqueue)

    result = asyncio.run(
        api_main.run_benchmark_suite(
            payload={"queue": True, "candidate_change_id": "queued_candidate"},
            storage=storage,
        )
    )

    assert result["status"] == "already_queued"
    assert result["task_id"] == existing.task_id
    assert result["kind"] == "benchmark"


def test_run_eval_job_task_completes_benchmark_job(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Queued benchmark",
        description="run benchmark",
        state=TaskState.PENDING,
        type=TaskType.JUDGE,
        constraints={
            "system_job": {
                "kind": "benchmark",
                "payload": {"candidate_change_id": "queued_candidate", "run_label": "queued-run"},
            }
        },
    )
    storage.commit()

    async def fake_run_benchmark(**_kwargs):
        return {
            "run_label": "queued-run",
            "prompt_count": 1,
            "baseline_wins": 0,
            "harness_wins": 1,
            "ties": 0,
            "average_baseline_score": 5.0,
            "average_harness_score": 8.0,
            "samples": [
                {
                    "prompt_id": "p1",
                    "winner": "harness",
                    "baseline_score": 5.0,
                    "harness_score": 8.0,
                }
            ],
        }

    monkeypatch.setattr("strata.eval.job_runner.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("strata.eval.job_runner.persist_benchmark_report", lambda *args, **kwargs: None)

    result = asyncio.run(run_eval_job_task(task, storage, model_adapter=None))
    storage.session.expire_all()
    reloaded_task = storage.tasks.get_by_id(task.task_id)

    assert result["harness_wins"] == 1
    assert reloaded_task.state == TaskState.COMPLETE
    assert reloaded_task.constraints["system_job_result"]["status"] == "completed"
    assert reloaded_task.constraints["system_job_result"]["kind"] == "benchmark"


def test_run_eval_job_task_records_full_eval_source_task(monkeypatch):
    storage = make_storage()
    task = storage.tasks.create(
        title="Queued full eval",
        description="run full eval",
        state=TaskState.PENDING,
        type=TaskType.JUDGE,
        constraints={
            "system_job": {
                "kind": "full_eval",
                "payload": {"candidate_change_id": "candidate_eval"},
            }
        },
    )
    storage.commit()

    async def fake_run_full_eval_gate(*args, **kwargs):
        from strata.experimental.experiment_runner import ExperimentResult
        assert kwargs["source_task_id"] == task.task_id
        return ExperimentResult(
            success=True,
            valid=True,
            candidate_change_id="candidate_eval",
            baseline_metrics={},
            candidate_metrics={},
            deltas={"benchmark_score_delta": 1.0},
            recommendation="promote",
            notes="ok",
        )

    monkeypatch.setattr("strata.eval.job_runner.ExperimentRunner.run_full_eval_gate", fake_run_full_eval_gate)

    result = asyncio.run(run_eval_job_task(task, storage, model_adapter=None))
    storage.session.expire_all()
    reloaded_task = storage.tasks.get_by_id(task.task_id)

    assert result["recommendation"] == "promote"
    assert reloaded_task.constraints["system_job_result"]["status"] == "completed"


def test_eval_jobs_endpoint_lists_queued_jobs():
    storage = make_storage()
    task = storage.tasks.create(
        title="Queued matrix eval",
        description="matrix",
        state=TaskState.PENDING,
        type=TaskType.JUDGE,
        constraints={
            "system_job": {"kind": "eval_matrix", "payload": {"suite_name": "mmlu_mini_v1"}},
            "system_job_result": {"status": "queued"},
            "generated_reports": [{"kind": "eval_matrix"}],
        },
    )
    storage.commit()

    result = asyncio.run(api_main.list_eval_jobs(active_only=True, limit=10, storage=storage))

    assert result["status"] == "ok"
    assert result["jobs"][0]["task_id"] == task.task_id
    assert result["jobs"][0]["system_job"]["kind"] == "eval_matrix"
    assert result["jobs"][0]["system_job_result"]["status"] == "queued"


def test_tasks_endpoint_includes_system_job_fields():
    storage = make_storage()
    task = storage.tasks.create(
        title="Queued structured eval",
        description="structured",
        state=TaskState.PENDING,
        type=TaskType.JUDGE,
        constraints={
            "system_job": {"kind": "structured_eval", "payload": {"suite_name": "bootstrap_mcq_v1"}},
            "system_job_result": {"status": "completed"},
            "generated_reports": [{"kind": "structured_eval"}],
        },
    )
    storage.commit()

    result = asyncio.run(api_main.list_tasks(storage=storage))
    matching = next(item for item in result if item["id"] == task.task_id)

    assert matching["system_job"]["kind"] == "structured_eval"
    assert matching["system_job_result"]["status"] == "completed"
    assert matching["generated_reports"][0]["kind"] == "structured_eval"
