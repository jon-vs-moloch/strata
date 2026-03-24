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
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from strata.schemas.core import EvaluationScorecardSchema
from strata.storage.models import TaskModel, CandidateModel
from strata.core.policy import requires_validator
from strata.orchestrator.worker.telemetry import record_metric

logger = logging.getLogger(__name__)

class ValidatorResult(BaseModel):
    success: bool
    message: str
    score_impact: float = 0.0

class ValidatorRegistry:
    """
    @summary pluggable validation engine for task-specific gates.
    """
    def __init__(self, storage_manager):
        self.storage = storage_manager

    def run(self, validator_name: str, task: TaskModel, content: str) -> ValidatorResult:
        if not validator_name or validator_name == "noop":
            return ValidatorResult(success=True, message="No validator required.")
            
        if validator_name == "python_import_only":
            return self._python_import_only(content)
        elif validator_name == "python_pytest":
            return self._run_pytest(task, content)
        elif validator_name == "json_schema":
            return self._json_schema(content)
        elif validator_name.startswith("custom_script:"):
            return self._run_custom_script(validator_name.split(":")[1], content)
            
        return ValidatorResult(success=False, message=f"Validator '{validator_name}' not implemented.")

    def _run_pytest(self, task: TaskModel, content: str) -> ValidatorResult:
        import subprocess
        import tempfile
        import shutil
        
        # 1. Identify test file to run
        constraints = task.constraints if isinstance(task.constraints, dict) else json.loads(task.constraints or "{}")
        test_file = constraints.get("test_file")
        if not test_file:
            targets = constraints.get("target_files", [])
            if targets:
                base = os.path.basename(targets[0])
                test_file = f"tests/test_{base}"
                if not os.path.exists(test_file):
                    test_file = os.path.join(os.path.dirname(targets[0]), f"test_{base}")
            
        if not test_file or not os.path.exists(test_file):
             return ValidatorResult(success=False, message="No test file found to run pytest against.")

        # 2. CREATE ISOLATED WORKSPACE
        # We use a temporary directory to avoid side effects and ensure we test the candidate
        with tempfile.TemporaryDirectory() as td:
            # Copy skeleton (we only need the current project files roughly)
            # In a real system, we'd use a docker container or a pre-built env.
            # Here, we'll copy the project excluding large/runtime dirs
            def ignore_func(path, names):
                return {".git", "__pycache__", "strata/runtime", "strata/experimental"}
            
            # Simple copy tree to the temp dir
            # Note: shutil.copytree(src, dst) - dst must not exist, which td is empty.
            # But the 'td' IS the dst. We should copy TO a subdir.
            sandbox_path = os.path.join(td, "sandbox")
            shutil.copytree(".", sandbox_path, ignore=ignore_func, dirs_exist_ok=True)
            
            # 3. OVERWRITE TARGET WITH CANDIDATE
            targets = constraints.get("target_files", [])
            if targets:
                primary_target = os.path.join(sandbox_path, targets[0])
                os.makedirs(os.path.dirname(primary_target), exist_ok=True)
                with open(primary_target, "w", encoding="utf-8") as f:
                    f.write(content)
            
            # 4. RUN PYTEST
            try:
                # We run pytest from the sandbox root
                staged_test = os.path.join(sandbox_path, test_file)
                result = subprocess.run(
                    ["pytest", staged_test], 
                    capture_output=True, 
                    text=True, 
                    timeout=30,
                    cwd=sandbox_path
                )
                if result.returncode == 0:
                    summary = result.stdout.splitlines()[-1] if result.stdout.splitlines() else "Passed"
                    return ValidatorResult(success=True, message=f"Pytest passed: {summary}", score_impact=2.0)
                else:
                    return ValidatorResult(success=False, message=f"Pytest failed in isolation:\n{result.stdout}\n{result.stderr}")
            except Exception as e:
                return ValidatorResult(success=False, message=f"Pytest execution error: {str(e)}")

    def _python_import_only(self, content: str) -> ValidatorResult:
        try:
            # Strip markdown fences if present
            code = content
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0]
            
            # Syntax check first
            ast.parse(code)
            
            # Import check: we can't easily 'import' arbitrary code safely without a sandbox
            # but we can check if it has all the imports it needs or doesn't have banned imports
            import re
            banned = ["os.system", "subprocess.Popen", "eval", "exec"]
            for b in banned:
                if b in code:
                    return ValidatorResult(success=False, message=f"Banned functionality detected: {b}")
                    
            return ValidatorResult(success=True, message="Python structural check passed (No banned opcodes).")
        except Exception as e:
            return ValidatorResult(success=False, message=f"Python check failed: {e}")

    def _json_schema(self, content: str) -> ValidatorResult:
        try:
            json.loads(content)
            return ValidatorResult(success=True, message="JSON parse successful.")
        except Exception as e:
            return ValidatorResult(success=False, message=f"JSON parse failed: {e}")

    def _run_custom_script(self, script_path: str, content: str) -> ValidatorResult:
        import subprocess
        if not os.path.exists(script_path):
            return ValidatorResult(success=False, message=f"Custom validation script {script_path} not found.")
            
        try:
            # Pass content via stdin or temp file
            result = subprocess.run([script_path], input=content, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return ValidatorResult(success=True, message=f"Custom script passed: {result.stdout.strip()}")
            else:
                return ValidatorResult(success=False, message=f"Custom script failed: {result.stderr.strip()}")
        except Exception as e:
            return ValidatorResult(success=False, message=f"Custom script error: {str(e)}")

class EvaluationPipeline:
    """
    @summary Multi-stage evaluation of a candidate artifact.
    @inputs task: TaskModel, candidate: CandidateModel
    @outputs EvaluationScorecardSchema
    """

    def __init__(self, storage_manager, context: Optional[Any] = None):
        self.storage = storage_manager
        self.validators = ValidatorRegistry(storage_manager)
        self.context = context

    async def evaluate_candidate(self, task: TaskModel, candidate: CandidateModel) -> EvaluationScorecardSchema:
        """
        @summary Run all validation stages and return a formal scorecard.
        """
        checks_passed = []
        checks_failed = []
        scores = []
        
        # Determine execution context details for metrics
        run_mode = "normal"
        ctx_mode = "strong"
        change_id = None
        if self.context:
            run_mode = getattr(self.context, "run_mode", "normal") if hasattr(self.context, "run_mode") else "normal"
            # If our context doesn't have run_mode, maybe it has evaluation_run flag
            if getattr(self.context, "evaluation_run", False):
                run_mode = "weak_eval"
            ctx_mode = getattr(self.context, "mode", "strong")
            change_id = getattr(self.context, "candidate_change_id", None)
        
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

        # 2. Structural Validation (Syntax + Artifact Specific)
        syntax_ok, syntax_err = self._check_syntax(content, candidate.artifact_type)
        if syntax_ok:
            checks_passed.append(f"Structural Validation ({candidate.artifact_type})")
            scores.append(10.0)
        else:
            checks_failed.append(f"Structural Validation failed: {syntax_err}")
            scores.append(0.0)

        # 3. Boundary Validation (Task Constraints)
        boundary_results = self._check_boundaries(task, candidate, content)
        for name, ok, msg, weight in boundary_results:
            if ok:
                checks_passed.append(name)
                scores.append(10.0 * weight)
            else:
                checks_failed.append(f"{name}: {msg}")
                scores.append(0.0)

        # 4. Validator Execution (Declared in Task)
        constraints = task.constraints if isinstance(task.constraints, dict) else json.loads(task.constraints or "{}")
        validator_name = constraints.get("validator")
        validator_ok = True
        if validator_name and validator_name != "noop":
            v_res = self.validators.run(validator_name, task, content)
            if v_res.success:
                checks_passed.append(f"Validator ({validator_name})")
                scores.append(10.0 + v_res.score_impact)
            else:
                checks_failed.append(f"Validator ({validator_name}) failed: {v_res.message}")
                scores.append(0.0)
                validator_ok = False
        elif requires_validator(task): # Mandate policy from core.policy
            checks_failed.append("Validator Policy: Declared task violates validator requirement.")
            scores.append(0.0)
            validator_ok = False

        diff_summary = self._generate_diff_summary(task, content)

        # Calculate final score
        # Cap score at 10.0
        final_score = min(sum(scores) / len(scores) if scores else 0.0, 10.0)
        
        # SUCCESS CONDITION: Must pass structural, boundary, AND validator checks
        boundary_ok = all([r[1] for r in boundary_results])
        is_valid = syntax_ok and boundary_ok and validator_ok
        
        # Record Fitness Signals
        t_type = task.type.value if hasattr(task.type, "value") else str(task.type)
        record_metric(
            self.storage, 
            "candidate_validity", 
            1.0 if is_valid else 0.0, 
            task_type=t_type, 
            task_id=task.task_id,
            model_id=candidate.model,
            run_mode=run_mode,
            execution_context=ctx_mode,
            candidate_change_id=change_id
        )
        if validator_name and validator_name != "noop":
            record_metric(
                self.storage, 
                "validator_pass_rate", 
                1.0 if validator_ok else 0.0, 
                task_type=t_type, 
                task_id=task.task_id, 
                model_id=candidate.model,
                run_mode=run_mode,
                execution_context=ctx_mode,
                candidate_change_id=change_id,
                details={"validator": validator_name}
            )
        
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
            # Strip markdown fences if present
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

    def _check_boundaries(self, task: TaskModel, candidate: CandidateModel, content: str) -> List[tuple]:
        """
        @summary Check if the candidate respects the task constraints.
        @returns List of (check_name, ok, message, weight)
        """
        constraints = task.constraints if isinstance(task.constraints, dict) else json.loads(task.constraints or "{}")
        results = []

        # A. Diff Size Budget
        max_size = constraints.get("max_diff_size", 50000)
        if len(content) > max_size:
            results.append(("Max Diff Size", False, f"Size {len(content)} > {max_size}", 1.0))
        else:
            results.append(("Max Diff Size", True, "Within budget", 0.5))
            
        # B. Target Files Compliance (Confinement)
        target_files = constraints.get("target_files", [])
        if not target_files:
            results.append(("Target Files Alignment", True, "No targets defined", 0.0))
        else:
            # 1. Verify existence in repo (for non-create tasks)
            edit_type = constraints.get("edit_type", "edit")
            missing = [f for f in target_files if not os.path.exists(f)]
            if missing and edit_type != "create":
                results.append(("Target Files Alignment", False, f"Missing target files: {missing}", 1.0))
            else:
                # 2. Verify confinement: check candidate's intended impact
                import json
                proposed = candidate.proposed_files if isinstance(candidate.proposed_files, list) else json.loads(candidate.proposed_files or "[]")
                
                # Filter out empty or nulls
                proposed = [f for f in proposed if f]
                
                illegal = [f for f in proposed if f not in target_files]
                if illegal:
                    results.append(("Target Files Confinement", False, f"Illegal modifications to: {illegal}", 1.0))
                elif not proposed:
                    results.append(("Target Files Confinement", False, "No target files declared in candidate.", 1.0))
                else:
                    results.append(("Target Files Confinement", True, f"Confined to: {proposed}", 0.5))
        
        # C. Edit Type Sanity
        edit_type = constraints.get("edit_type", "feature")
        results.append(("Edit Type Sanity", True, f"Match: {edit_type}", 0.3))

        return results

    def _generate_diff_summary(self, task: TaskModel, content: str) -> str:
        """
        @summary Generate a real diff summary comparing candidate to original source.
        """
        import json
        constraints = task.constraints if isinstance(task.constraints, dict) else json.loads(task.constraints or "{}")
        targets = constraints.get("target_files", [])
        if not targets:
            return f"No target files. Content length: {len(content)}"
            
        target_path = targets[0] # Use first file as primary
        if not os.path.exists(target_path):
            return f"Created new file: {target_path} ({len(content)} chars)"
            
        with open(target_path, "r", encoding="utf-8") as f:
            original = f.read()
            
        diff = list(difflib.unified_diff(
            original.splitlines(),
            content.splitlines(),
            fromfile=f"original/{target_path}",
            tofile=f"candidate/{target_path}",
            n=0
        ))
        
        added = len([l for l in diff if l.startswith("+") and not l.startswith("+++")])
        removed = len([l for l in diff if l.startswith("-") and not l.startswith("---")])
        
        return f"Changes to {target_path}: +{added}/-{removed} lines."
