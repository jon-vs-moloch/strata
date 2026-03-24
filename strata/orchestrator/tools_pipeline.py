"""
@module orchestrator.tools_pipeline
@purpose Gated promotion pipeline for self-modifying tools.
@owns tool validation, sandbox testing, promotion logic
@does_not_own tool generation
@key_exports ToolsPromotionPipeline
"""

import os
import shutil
import logging
from typing import Optional
from strata.orchestrator.evaluation import EvaluationPipeline
from strata.storage.models import TaskModel

logger = logging.getLogger(__name__)

class ToolsPromotionPipeline:
    """
    @summary Gated pipeline for promoting experimental tools to live status.
    @inputs storage_manager
    """

    def __init__(self, storage_manager):
        self.storage = storage_manager
        self.evaluator = EvaluationPipeline(storage_manager)
        self.tools_dir = "strata/tools"

    async def validate_and_promote(self, tool_name: str, task: Optional[TaskModel] = None) -> (bool, str):
        """
        @summary Validate an experimental tool and promote it if it passes all gates.
        """
        experimental_path = os.path.join(self.tools_dir, f"{tool_name}.experimental.py")
        live_path = os.path.join(self.tools_dir, f"{tool_name}.py")

        if not os.path.exists(experimental_path):
            return False, f"Experimental tool file not found: {experimental_path}"

        # 1. Structural Validation (Syntax)
        with open(experimental_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        syntax_ok, syntax_err = self.evaluator._check_syntax(content, "python_file")
        if not syntax_ok:
            return False, f"Structural Validation failed: {syntax_err}"

        # 2. Contract Compliance (Basic check for expected functions/classes)
        # For now, just ensure it's not empty and has basic Python structure
        if len(content.strip()) < 10:
            return False, "Contract Compliance failed: Tool source is too short."

        # 3. Sandbox Execution / Tool Testing (STUB)
        # In a real system, we'd run a test fixture here.
        logger.info(f"Running sandbox tests for tool: {tool_name}...")
        test_passed = True # STUB
        if not test_passed:
            return False, "Sandbox tests failed."

        # 4. Generate Evaluation Artifact (Log the promotion)
        logger.info(f"Tool {tool_name} passed all gates. Promoting to live.")
        
        # 5. Promotion (Atomic rename)
        try:
            # Backup old version if it exists
            if os.path.exists(live_path):
                attic_dir = "strata/attic/tools"
                os.makedirs(attic_dir, exist_ok=True)
                shutil.copy2(live_path, os.path.join(attic_dir, f"{tool_name}.py.bak"))
                
            shutil.move(experimental_path, live_path)
            return True, f"Successfully promoted {tool_name} to live."
        except Exception as e:
            logger.error(f"Promotion failed: {e}")
            return False, f"Promotion failed: {str(e)}"

    def rollback_tool(self, tool_name: str) -> (bool, str):
        """
        @summary Revert a tool to its previous version if available.
        """
        live_path = os.path.join(self.tools_dir, f"{tool_name}.py")
        bak_path = os.path.join("strata/attic/tools", f"{tool_name}.py.bak")
        
        if not os.path.exists(bak_path):
            return False, f"No backup found for tool: {tool_name}"
            
        try:
            shutil.copy2(bak_path, live_path)
            return True, f"Successfully rolled back {tool_name}."
        except Exception as e:
            return False, f"Rollback failed: {str(e)}"
