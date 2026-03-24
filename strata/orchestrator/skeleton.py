"""
@module orchestrator.skeleton
@purpose Primary loop for coordinating multi-candidate coding workflows.
@owns task lifecycle, stage transitions, worker parallelization
@does_not_own code synthesis logic, evaluation sandboxes, git operations
@key_exports SkeletonOrchestrator
"""

import asyncio
from typing import List, Optional
from uuid import uuid4
from datetime import datetime

from strata.schemas.core import ResearchReport, TaskDecomposition
from strata.orchestrator.research import ResearchModule
from strata.orchestrator.scheduler import SchedulerModule
from strata.orchestrator.decomposition import DecompositionModule
from strata.orchestrator.implementation import ImplementationModule
from strata.orchestrator.judge import JudgeModule
from strata.orchestrator.synthesis import SynthesisModule
from strata.storage.models import TaskStatus

class SkeletonOrchestrator:
    """
    @summary Coordinates the full Strata task execution flow.
    @inputs storage_manager, model_adapter, sandbox_provider
    @outputs side-effect driven (updates DB tasks, creates candidates)
    @side_effects creates worktrees, writes to DB, triggers LLM calls
    @depends storage.manager, models.adapter, sandbox.local
    """
    
    def __init__(self, storage, model, sandbox):
        """
        @summary Initialize orchestrator with core dependencies.
        @inputs storage: StorageManager instance, model: ModelAdapter, sandbox: SandboxProvider
        @outputs none
        """
        self.storage = storage
        self.model = model
        self.sandbox = sandbox
        self.researcher = ResearchModule(model_adapter=model, storage_manager=storage)
        self.scheduler = SchedulerModule(storage_manager=storage)
        self.decomposer = DecompositionModule(model_adapter=model, storage_manager=storage)
        self.executor = ImplementationModule(model_adapter=model, storage_manager=storage, research_module=self.researcher)
        self.judger = JudgeModule(model_adapter=model, storage_manager=storage)
        self.synthesizer = SynthesisModule(model_adapter=model, storage_manager=storage)

    async def process_task(self, task_id: str):
        """
        @summary Execute the orchestrator lifecycle for a given task.
        @inputs task_id: DB primary key for the task
        @outputs boolean indicating success/failure of the root process
        @side_effects triggers decomposition, implementation candidates, and evaluation
        @invariants task status correctly updated at each stage.
        """
        # 🟢 Priority-Aware Step: Pull the Next Best Task
        task = self.scheduler.get_next_runnable_task()
        if not task:
            print("No runnable tasks. Swarm in idle state.")
            return False

        print(f"Orchestrating task: {task.task_id} ({task.title})")
        
        # Phase 1: GLOBAL Research (Arch & Constraints)
        global_research: ResearchReport = await self.researcher.conduct_research(
            task_description=f"Initial research for root task: {task.title}"
        )
        print(f"Global research complete. {len(global_research.key_constraints_discovered)} constraints found.")

        # Phase 2: Frame & Decompose (if parent)
        decomposition: TaskDecomposition = await self.decomposer.decompose_task(
            task_title=task.title,
            task_desc=task.description,
            research=global_research
        )
        print(f"Decomposition complete. Found {len(decomposition.subtasks)} subtasks.")

        # Phase 3: Leaf-Level Implementation with LOCAL Research
        # In a real run, this would loop over decomposition.subtasks in parallel
        for sub_id, sub_proto in decomposition.subtasks.items():
             # Creating child task records in DB
             child = self.storage.tasks.create(
                 parent_task_id=task.task_id,
                 title=sub_proto.title,
                 description=sub_proto.description,
                 depth=task.depth + 1
             )
             self.storage.commit()
             
             # IMPLEMENT: Includes Second 'Local' Research Pass internally
             candidate_ids = await self.executor.implement_task(
                 task_id=child.task_id, 
                 global_research=global_research
             )
             
             # Phase 4: Parallelize Evaluation & Judge
             rankings = await self.judger.judge_candidates(child.task_id, candidate_ids)
             print(f"Judged {len(rankings)} candidates for subtask {sub_id}.")

        # Phase 5: Synthesis & Promotion
        # Final pass merging all child patches
        # synthesis_patch = await self.synthesizer.synthesize_subtasks(task.task_id, ...)
        
        # Phase 6: Terminate
        self.storage.tasks.update_status(task.task_id, TaskStatus.COMPLETED)
        self.storage.commit()
        
        return True

    def _build_implementation_prompt(self, task, framing) -> str:
        """
        @summary Construct the YAML-formatted prompt for worker agents.
        @inputs task: TaskModel, framing: TaskFraming
        @outputs raw prompt string for LLM
        @side_effects reads prompt registry
        """
        # Fetching v1 impl prompt from registry
        return f"TASK: {task.title}\nGOAL: {task.description}"
