"""
@module orchestrator.evaluation
@purpose Deterministic evaluation pipeline for candidate implementations.
@owns structural validation, boundary validation, scorecard generation
@does_not_own code execution (sandboxing)
@key_exports EvaluationPipeline
"""

import ast
import os
import difflib
import logging
from typing import List, Dict, Any, Optional
from strata.schemas.core import EvaluationScorecardSchema
from strata.storage.models import TaskModel, CandidateModel

logger = logging.getLogger(__name__)

class EvaluationPipeline:
    """
    @summary Multi-stage evaluation of a candidate artifact.
    @inputs task: TaskModel, candidate: CandidateModel
    @outputs EvaluationScorecardSchema
    """

    def __init__(self, storage_manager):
        self.storage = storage_manager

    async def evaluate_candidate(self, task: TaskModel, candidate: CandidateModel) -> EvaluationScorecardSchema:
        """
        @summary Run all validation stages and return a formal scorecard.
        """
        checks_passed = []
        checks_failed = []
        scores = []
        
        # 1. Read candidate content
        content = ""
        if os.path.exists(candidate.content_path):
            with open(candidate.content_path, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            return EvaluationScorecardSchema(
                valid=False,
                checks_passed=[],
                checks_failed=["File missing"],
                diff_summary="Candidate artifact file not found on disk.",
                score=0.0,
                reasoning="Artifact file is missing."
            )

        # 2. Structural Validation (Syntax)
        syntax_ok, syntax_err = self._check_syntax(content, candidate.artifact_type)
        if syntax_ok:
            checks_passed.append("Structural Validation (Syntax)")
            scores.append(10.0)
        else:
            checks_failed.append(f"Structural Validation (Syntax): {syntax_err}")
            scores.append(0.0)

        # 3. Boundary Validation (Controlled files and size)
        boundary_ok, boundary_msg = self._check_boundaries(task, content)
        if boundary_ok:
            checks_passed.append("Boundary Validation")
            scores.append(10.0)
        else:
            checks_failed.append(f"Boundary Validation: {boundary_msg}")
            scores.append(5.0) # Penalty but not necessarily invalidating if it's just a size issue

        # 4. Generate Diff Summary
        diff_summary = self._generate_diff_summary(task, content)

        # Calculate final score
        final_score = sum(scores) / len(scores) if scores else 0.0
        
        # SUCCESS CONDITION: Must pass structural validation to be valid
        is_valid = syntax_ok
        
        return EvaluationScorecardSchema(
            valid=is_valid,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            diff_summary=diff_summary,
            score=final_score,
            reasoning="Deterministic evaluation complete." if is_valid else f"Validation failed: {', '.join(checks_failed)}"
        )

    def _check_syntax(self, content: str, artifact_type: str) -> (bool, Optional[str]):
        if artifact_type != "python_file":
            return True, None # Skip for now
            
        try:
            # Strip markdown fences if present (implementation module handles this but being safe)
            code = content
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0]
            elif "```" in code:
                code = code.split("```")[1].split("```")[0]
                
            ast.parse(code)
            return True, None
        except SyntaxError as e:
            return False, f"SyntaxError at line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, str(e)

    def _check_boundaries(self, task: TaskModel, content: str) -> (bool, str):
        import json
        constraints = {}
        if task.constraints:
            try:
                constraints = json.loads(task.constraints) if isinstance(task.constraints, str) else task.constraints
            except:
                pass
                
        # Check diff size budget
        max_size = constraints.get("max_diff_size", 50000) # Default 50KB
        if len(content) > max_size:
            return False, f"Candidate size ({len(content)}) exceeds budget ({max_size})"
            
        # target_files check would go here if we were comparing against original
        return True, "Boundaries respected."

    def _generate_diff_summary(self, task: TaskModel, content: str) -> str:
        # For now, just a stub summary.
        # In a real system, we'd compare against task.repo_path + target_file
        return f"Modified content length: {len(content)} characters."
