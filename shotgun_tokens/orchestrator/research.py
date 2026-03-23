"""
@module orchestrator.research
@purpose Gather and synthesize contextual information prior to task execution or decomposition.
@owns metadata retrieval, documentation search, context synthesis
@does_not_own LLM API interactions directly (uses ModelAdapter), or task state mutations
@key_exports ResearchModule
@side_effects none
"""

from typing import Dict, Any, Optional
from shotgun_tokens.schemas.core import ResearchReport

class ResearchModule:
    """
    @summary Executes the research phase for a given task, querying repo metadata.
    @inputs model: ModelAdapter, storage: StorageManager
    @outputs ResearchReport containing synthesized context
    @side_effects requests completions from the LLM adapter
    @depends models.adapter, schemas.core.ResearchReport
    @invariants always returns a ResearchReport regardless of findings
    """
    def __init__(self, model_adapter, storage_manager):
        """
        @summary Initialize the ResearchModule.
        @inputs model_adapter instance, storage_manager instance
        @outputs none
        """
        self.model = model_adapter
        self.storage = storage_manager

    async def conduct_research(self, task_description: str, repo_path: str = None) -> ResearchReport:
        """
        @summary Conducts initial research to identify constraints and suggested approaches.
        @inputs task_description: what the agent is assigned to do, repo_path: paths to index
        @outputs A populated ResearchReport object
        @side_effects May read from local metadata index (symbols.yaml)
        """
        print(f"Conducting research for task: {task_description[:50]}...")
        
        # In a real run, this would query symbols.yaml or an index
        context = "Pre-computed agent metadata indicates the codebase uses SQLAlchemy for DB operations."
        constraints = ["Do not use raw SQL queries.", "Ensure models inherit from Base."]
        approach = "Review storage/models.py to understand existing relationships before implementing."

        return ResearchReport(
            context_gathered=context,
            key_constraints_discovered=constraints,
            suggested_approach=approach,
            reference_urls=["metadata/symbols.yaml"]
        )
