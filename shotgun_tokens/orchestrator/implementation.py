"""
@module orchestrator.implementation
@purpose Execute leaf-level coding tasks and generate implementation candidates.
@owns code generation, local research (file-level), staging of candidate artifacts
@does_not_own task decomposition, synthesis, or evaluation
@key_exports ImplementationModule
@side_effects initiates code writing to temporary worktrees or buffer files
"""

from typing import List, Dict, Any, Optional
from shotgun_tokens.schemas.core import ResearchReport, ResearchReport as LocalResearchReport

class ImplementationModule:
    """
    @summary Manages the leaf-level execution of a code transformation task.
    @inputs model: ModelAdapter, storage: StorageManager, researcher: ResearchModule
    @outputs List of candidate IDs
    @side_effects requests completions from the LLM adapter, writes candidates to storage
    @depends orchestrator.research.ResearchModule, models.adapter
    @invariants does not mutate the main branch (uses candidates/worktrees)
    """
    def __init__(self, model_adapter, storage_manager, research_module):
        """
        @summary Initialize the ImplementationModule.
        @inputs model_adapter instance, storage_manager instance, research_module instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager
        self.researcher = research_module

    async def implement_task(self, task_id: str, global_research: Optional[ResearchReport] = None) -> List[str]:
        """
        @summary Execute a coding task with a two-pass research strategy.
        """
        # Fetch task details from DB
        from shotgun_tokens.storage.models import TaskModel, CandidateModel
        task = self.storage.session.query(TaskModel).filter_by(task_id=task_id).first()
        if not task:
            return []
            
        print(f"Implementing leaf task: {task.title}...")
        
        # Pass 2: Local Research focused on the Files
        local_research: ResearchReport = await self.researcher.conduct_research(
            task_description=f"Analyze files {task.constraints.get('target_files', [])} to implement: {task.description}",
            repo_path=task.repo_path
        )

        # Get past failures to avoid infinite loops
        from shotgun_tokens.storage.models import AttemptModel, AttemptOutcome
        failed_attempts = self.storage.session.query(AttemptModel).filter(
            AttemptModel.task_id == task_id,
            AttemptModel.outcome == AttemptOutcome.FAILED
        ).order_by(AttemptModel.started_at.asc()).all()
        
        failure_log = "None."
        if failed_attempts:
            failure_log = "\n".join([
                f"- Attempt {i+1} Failed: {a.reason or 'Unknown error'}. Resolution: {a.resolution.value if a.resolution else 'None'}."
                for i, a in enumerate(failed_attempts)
            ])

        system_prompt = f"""You are an Senior Implementation Engineer. 
        Your goal is to write code that satisfies the following task:
        
        TITLE: {task.title}
        DESCRIPTION: {task.description}
        
        GLOBAL ARCHITECTURAL CONTEXT:
        {global_research.context_gathered if global_research else "None provided."}
        
        LOCAL IMPLEMENTATION DETAILS:
        {local_research.context_gathered}
        CONSTRAINTS: {local_research.key_constraints_discovered}
        
        PAST ATTEMPTS TO AVOID:
        {failure_log}
        
        YOU MUST OUTPUT THE ENTIRE UPDATED FILE CONTENT OR A NEW FILE CONTENT.
        Output format:
        ```python (or other language)
        [CODE HERE]
        ```
        """
        
        response = await self.model.chat([{"role": "user", "content": system_prompt}])
        content = response.get("content", "")
        
        # Create a candidate record
        import os
        from uuid import uuid4
        candidate_id = str(uuid4())
        
        candidate = CandidateModel(
            candidate_id=candidate_id,
            task_id=task_id,
            stage="impl",
            prompt_version="v1",
            model=self.model.active_model,
            artifact_type="python_file",
            content_path=f"candidates/{candidate_id}.py", # In a real system, use /tmp/ or a worktree
            summary=f"Implementation for {task.title}",
            proposed_files=task.constraints.get("target_files", [])
        )
        self.storage.session.add(candidate)
        self.storage.commit()
        
        # Write the actual file artifact for future judging
        os.makedirs("candidates", exist_ok=True)
        with open(candidate.content_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        return [candidate_id]

