"""
@module storage.repositories.tasks
@purpose High-level CRUD operations for the TaskModel entity.
@owns task creation, status updates, dependency management (SQL layer)
@does_not_own session lifecycle management, business logic orchestration
@key_exports TaskRepository
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from strata.storage.models import TaskModel, TaskState

class TaskRepository:
    """
    @summary Manages structured task storage and retrieval using SQLAlchemy.
    @inputs session: SQLAlchemy Session
    @outputs side-effect driven (DB mutations) or TaskModel objects
    @side_effects writes to 'tasks' and 'task_dependencies' tables
    @depends storage.models.TaskModel
    @invariants does not commit the session (left to orchestrator)
    """
    def __init__(self, session: Session):
        """
        @summary Initialize the TaskRepository.
        @inputs session: active DB session
        @outputs none
        """
        self.session = session

    def create(self, **kwargs) -> TaskModel:
        """
        @summary Instantiate and persist a new TaskModel.
        @inputs dictionary of task attributes
        @outputs the created TaskModel object
        @side_effects adds object to session
        """
        task = TaskModel(**kwargs)
        self.session.add(task)
        self.session.flush() # Ensure ID is generated
        return task

    def get_by_id(self, task_id: str) -> Optional[TaskModel]:
        """
        @summary Retrieve a task by its primary key.
        @inputs task_id: string UUID
        @outputs TaskModel or None
        """
        return self.session.get(TaskModel, task_id)

    def update_state(self, task_id: str, state: TaskState):
        """
        @summary Transition a task to a new state.
        @inputs task_id, state: TaskState enum
        @outputs none
        @side_effects updates database row
        """
        task = self.get_by_id(task_id)
        if task:
            task.state = state

    def add_dependency(self, task_id: str, depends_on_id: str):
        """
        @summary Add a dependency between two tasks.
        @inputs task_id (the dependent task), depends_on_id (the task being depended on)
        """
        task = self.get_by_id(task_id)
        dep = self.get_by_id(depends_on_id)
        if task and dep:
            if dep not in task.dependencies:
                task.dependencies.append(dep)
