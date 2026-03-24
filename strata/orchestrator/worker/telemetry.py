"""
@module orchestrator.worker.telemetry
@purpose Periodically summarize model performance intelligence and fitness signals.
"""

import os
import logging
from datetime import datetime
from sqlalchemy import func, select
from strata.storage.models import ModelTelemetry, AttemptModel, AttemptOutcome, TaskModel, TaskType

logger = logging.getLogger(__name__)

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
            .filter(TaskModel.type == TaskType.DECOMP, TaskModel.state == "complete")
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
