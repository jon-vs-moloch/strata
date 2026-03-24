"""
@module orchestrator.tools_pipeline
@purpose Gated promotion pipeline for self-modifying tools with smoke tests and rollbacks.
@owns tool validation, import checks, contract compliance, smoke testing, local promotion
"""

import os
import shutil
import logging
import importlib.util
import json
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from strata.orchestrator.evaluation import EvaluationPipeline
from strata.storage.models import TaskModel
from strata.orchestrator.worker.telemetry import record_metric

logger = logging.getLogger(__name__)

class PromotionResult(BaseModel):
    tool_name: str
    promoted: bool
    checks_passed: List[str]
    checks_failed: List[str]
    backup_path: Optional[str] = None
    rollback_available: bool = False
    details: str = ""

class ToolsPromotionPipeline:
    """
    @summary Gated pipeline for promoting experimental tools to live status.
    """

    def __init__(self, storage_manager):
        self.storage = storage_manager
        self.evaluator = EvaluationPipeline(storage_manager)
        self.tools_dir = "strata/tools"
        self.tests_dir = os.path.join(self.tools_dir, "tests")
        self.manifest_dir = os.path.join(self.tools_dir, "manifests")
        self.attic_tools_dir = "strata/attic/tools"
        
        os.makedirs(self.tests_dir, exist_ok=True)
        os.makedirs(self.manifest_dir, exist_ok=True)
        os.makedirs(self.attic_tools_dir, exist_ok=True)

    async def validate_and_promote(self, tool_name: str, task: Optional[TaskModel] = None) -> PromotionResult:
        """
        @summary Full stage-gate validation for tool promotion.
        """
        experimental_path = os.path.join(self.tools_dir, f"{tool_name}.experimental.py")
        live_path = os.path.join(self.tools_dir, f"{tool_name}.py")
        checks_passed = []
        checks_failed = []

        if not os.path.exists(experimental_path):
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=[], checks_failed=["File missing"], details=f"Experimental tool file not found: {experimental_path}")

        # 1. READ CONTENT
        with open(experimental_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 2. STAGE: SYNTAX VALIDATION
        syntax_ok, syntax_err = self.evaluator._check_syntax(content, "python_file")
        if syntax_ok:
            checks_passed.append("Syntax Validation")
        else:
            checks_failed.append(f"Syntax Validation: {syntax_err}")
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed)

        # 3. LOAD MANIFEST (MANDATORY)
        manifest_path = os.path.join(self.manifest_dir, f"{tool_name}.json")
        if not os.path.exists(manifest_path):
            checks_failed.append("Promotion Policy: Missing mandatory tool manifest.")
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed)
            
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
            
        # 4. STAGE: IMPORT VALIDATION
        try:
            spec = importlib.util.spec_from_file_location(f"temp_tool_{tool_name}", experimental_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            checks_passed.append("Import Validation")
        except Exception as e:
            checks_failed.append(f"Import Validation: {e}")
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed)
            
        # 5. STAGE: CONTRACT & VALIDATOR POLICY
        validator_name = manifest.get("validator")
        if not validator_name:
            checks_failed.append("Promotion Policy: Manifest missing declared validator.")
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed)
        
        # Check if validator exists in registry
        v_res = self.evaluator.validators.run(validator_name, None, content)
        if v_res.success:
            checks_passed.append(f"Contract Validation ({validator_name})")
        else:
            checks_failed.append(f"Contract Validation ({validator_name}) failed: {v_res.message}")
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed)

        # 6. STAGE: SMOKE TEST
        smoke_test_path = manifest.get("smoke_test") or os.path.join(self.tests_dir, f"test_{tool_name}_smoke.py")
        if os.path.exists(smoke_test_path):
            test_passed, test_msg = self._run_smoke_test(smoke_test_path, tool_name)
            if test_passed:
                checks_passed.append("Smoke Test")
            else:
                checks_failed.append(f"Smoke Test: {test_msg}")
                return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed)
        else:
            checks_failed.append(f"Smoke Test: Missing required smoke test fixture at {smoke_test_path}.")
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed)

        # 6. STAGE: BACKUP AND PROMOTE
        backup_path = None
        try:
            if os.path.exists(live_path):
                backup_path = os.path.join(self.attic_tools_dir, f"{tool_name}.py.bak")
                shutil.copy2(live_path, backup_path)
            
            shutil.move(experimental_path, live_path)
            checks_passed.append("Promotion")
            
            # 8. UPDATE MANIFEST
            manifest["timestamp"] = str(os.path.getmtime(live_path))
            manifest["checks_passed"] = checks_passed
            with open(os.path.join(self.manifest_dir, f"{tool_name}.json"), "w") as f:
                json.dump(manifest, f, indent=2)
                
            record_metric(self.storage, "tool_promotion_success", 1.0, details={"tool_name": tool_name})
            return PromotionResult(
                tool_name=tool_name,
                promoted=True,
                checks_passed=checks_passed,
                checks_failed=checks_failed,
                backup_path=backup_path,
                rollback_available=backup_path is not None,
                details=f"Successfully promoted {tool_name} to live."
            )
        except Exception as e:
            record_metric(self.storage, "tool_promotion_success", 0.0, details={"tool_name": tool_name, "error": str(e)})
            checks_failed.append(f"Promotion Execution: {e}")
            return PromotionResult(tool_name=tool_name, promoted=False, checks_passed=checks_passed, checks_failed=checks_failed, details=str(e))

    def _run_smoke_test(self, test_path: str, tool_name: str) -> (bool, str):
        """
        @summary Execute a tool-specific smoke test.
        """
        import subprocess
        try:
            # We assume the smoke test is a python script that exits with 0 on success
            result = subprocess.run(["python", test_path], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return True, "Passed"
            else:
                return False, result.stderr or result.stdout
        except subprocess.TimeoutExpired:
            return False, "Timed out during smoke test."
        except Exception as e:
            return False, str(e)

    def rollback_tool(self, tool_name: str) -> (bool, str):
        """
        @summary Revert a tool to its previous version if available.
        """
        live_path = os.path.join(self.tools_dir, f"{tool_name}.py")
        bak_path = os.path.join(self.attic_tools_dir, f"{tool_name}.py.bak")
        
        if not os.path.exists(bak_path):
            return False, f"No backup found for tool: {tool_name}"
            
        try:
            shutil.copy2(bak_path, live_path)
            return True, f"Successfully rolled back {tool_name}."
        except Exception as e:
            return False, f"Rollback failed: {str(e)}"
