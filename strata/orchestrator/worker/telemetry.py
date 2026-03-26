"""
@module orchestrator.worker.telemetry
@purpose Periodically summarize model performance intelligence and fitness signals.

Telemetry is a core control surface in Strata, not just a debugging aid.
The system uses recorded outcomes to decide whether harness changes are
actually helping weak models become more capable over time.
"""

import os
import logging
from datetime import datetime
from sqlalchemy import func, desc
from strata.storage.models import ModelTelemetry, AttemptModel, AttemptOutcome, TaskModel, TaskType, TaskState, MetricModel

logger = logging.getLogger(__name__)

def record_metric(
    storage,
    metric_name: str,
    value: float,
    model_id: str | None = None,
    task_type: str | None = None,
    task_id: str | None = None,
    details: dict | None = None,
    run_mode: str | None = None, # New
    execution_context: str | None = None, # New
    candidate_change_id: str | None = None # New
):
    """
    @summary Persist a structured architectural fitness signal.
    """
    try:
        metric = MetricModel(
            metric_name=metric_name,
            value=value,
            model_id=model_id,
            task_type=task_type,
            run_mode=run_mode,
            execution_context=execution_context,
            candidate_change_id=candidate_change_id,
            details=details or {}
        )
        if task_id:
            metric.details["task_id"] = task_id
            
        storage.session.add(metric)
        storage.commit()
    except Exception as e:
        logger.error(f"Failed to record metric {metric_name}: {e}")

async def synthesize_model_performance(storage_factory):
    """
    @summary Analyze telemetry and attempt history to generate a fitness signal report.
    """
    storage = storage_factory()
    try:
        # 1. Basic Model Performance Score (by Task Type)
        perf_results = (
            storage.session.query(
                ModelTelemetry.model_id,
                ModelTelemetry.task_type,
                func.avg(ModelTelemetry.score).label("avg_score"),
                func.count(ModelTelemetry.id).label("sample_size")
            )
            .group_by(ModelTelemetry.model_id, ModelTelemetry.task_type)
            .all()
        )
        
        # 2. Advanced Weak-Model Capability Metrics
        # (Assuming 'weak' models are identified by name, e.g., 'gpt-3.5-turbo' or 'hermes')
        # For now we'll just report on all models.
        
        total_attempts = storage.session.query(func.count(AttemptModel.attempt_id)).scalar() or 0
        successful_attempts = storage.session.query(func.count(AttemptModel.attempt_id)).filter(AttemptModel.outcome == AttemptOutcome.SUCCEEDED).scalar() or 0
        failed_attempts = total_attempts - successful_attempts
        
        success_rate = (successful_attempts / total_attempts * 100) if total_attempts > 0 else 0
        
        # Decomposition success rate (Successes in tasks spawned from DECOMP)
        decomp_success = (
            storage.session.query(func.count(TaskModel.task_id))
            .filter(TaskModel.type == TaskType.DECOMP, TaskModel.state == TaskState.COMPLETE)
            .scalar() or 0
        )
        
        md = [
            "# Strata Fitness Signal Report",
            f"*Generated on: {datetime.utcnow().isoformat()}*",
            "\n## Global Stats",
            f"- **Total Attempts:** {total_attempts}",
            f"- **Success Rate:** {success_rate:.2f}%",
            f"- **Failed Attempts:** {failed_attempts}",
            f"- **Decomposition Successes:** {decomp_success}",
            "\n## Model Breakdown"
        ]
        
        for r in perf_results:
            md.append(f"- **{r.model_id}** ({r.task_type}): {r.avg_score:.2f} avg score (n={r.sample_size})")
        
        md.append("\n## Weak-Model Optimization Targets")
        md.append("- [ ] Increase valid candidate rate for leaf IMPLEMENTATION tasks.")
        md.append("- [ ] Reduce retry depth for complex REFACTOR tasks.")
        
        os.makedirs(".knowledge", exist_ok=True)
        with open(".knowledge/model_performance_intel.md", "w") as f:
            f.write("\n".join(md))
            
        logger.info("Synthesized fitness signal report to .knowledge/model_performance_intel.md")
    except Exception as e:
        logger.error(f"Failed to synthesize model performance: {e}")
    finally:
        storage.session.close()


def build_telemetry_snapshot(storage, limit: int = 25) -> dict:
    """
    @summary Return a compact snapshot of the metrics that drive bootstrap decisions.
    @notes This is intentionally shaped for both the UI and agent-side introspection.
    """
    total_attempts = storage.session.query(func.count(AttemptModel.attempt_id)).scalar() or 0
    succeeded_attempts = (
        storage.session.query(func.count(AttemptModel.attempt_id))
        .filter(AttemptModel.outcome == AttemptOutcome.SUCCEEDED)
        .scalar()
        or 0
    )
    failed_attempts = (
        storage.session.query(func.count(AttemptModel.attempt_id))
        .filter(AttemptModel.outcome == AttemptOutcome.FAILED)
        .scalar()
        or 0
    )
    weak_eval_runs = (
        storage.session.query(func.count(MetricModel.id))
        .filter(MetricModel.run_mode == "weak_eval")
        .scalar()
        or 0
    )
    unique_experiments = (
        storage.session.query(func.count(func.distinct(MetricModel.candidate_change_id)))
        .filter(MetricModel.candidate_change_id.isnot(None))
        .scalar()
        or 0
    )

    success_rate = (succeeded_attempts / total_attempts * 100.0) if total_attempts else 0.0

    rollups = (
        storage.session.query(
            MetricModel.metric_name,
            func.count(MetricModel.id).label("count"),
            func.avg(MetricModel.value).label("avg_value"),
            func.max(MetricModel.timestamp).label("last_seen"),
        )
        .group_by(MetricModel.metric_name)
        .order_by(desc("last_seen"))
        .all()
    )

    recent_metrics = (
        storage.session.query(MetricModel)
        .order_by(MetricModel.timestamp.desc())
        .limit(limit)
        .all()
    )

    experiment_rollups = (
        storage.session.query(
            MetricModel.candidate_change_id,
            func.count(MetricModel.id).label("count"),
            func.avg(MetricModel.value).label("avg_value"),
            func.max(MetricModel.timestamp).label("last_seen"),
        )
        .filter(MetricModel.candidate_change_id.isnot(None))
        .group_by(MetricModel.candidate_change_id)
        .order_by(desc("last_seen"))
        .limit(10)
        .all()
    )

    weak_metric_rollups = (
        storage.session.query(
            MetricModel.metric_name,
            func.count(MetricModel.id).label("count"),
            func.avg(MetricModel.value).label("avg_value"),
        )
        .filter(MetricModel.execution_context == "weak")
        .group_by(MetricModel.metric_name)
        .order_by(desc("count"))
        .all()
    )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "overview": {
            "total_attempts": total_attempts,
            "succeeded_attempts": succeeded_attempts,
            "failed_attempts": failed_attempts,
            "success_rate": round(success_rate, 2),
            "weak_eval_runs": weak_eval_runs,
            "unique_experiments": unique_experiments,
        },
        "rollups": [
            {
                "metric_name": row.metric_name,
                "count": int(row.count),
                "avg_value": round(float(row.avg_value or 0.0), 4),
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
            }
            for row in rollups
        ],
        "weak_rollups": [
            {
                "metric_name": row.metric_name,
                "count": int(row.count),
                "avg_value": round(float(row.avg_value or 0.0), 4),
            }
            for row in weak_metric_rollups
        ],
        "experiments": [
            {
                "candidate_change_id": row.candidate_change_id,
                "count": int(row.count),
                "avg_value": round(float(row.avg_value or 0.0), 4),
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
            }
            for row in experiment_rollups
        ],
        "recent_metrics": [
            {
                "id": metric.id,
                "timestamp": metric.timestamp.isoformat() if metric.timestamp else None,
                "metric_name": metric.metric_name,
                "value": metric.value,
                "model_id": metric.model_id,
                "task_type": metric.task_type,
                "run_mode": metric.run_mode,
                "execution_context": metric.execution_context,
                "candidate_change_id": metric.candidate_change_id,
                "details": metric.details or {},
            }
            for metric in recent_metrics
        ],
    }
