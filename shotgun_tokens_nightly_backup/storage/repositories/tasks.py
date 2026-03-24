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
from shotgun_tokens.storage.models import TaskModel, TaskStatus

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

    def update_status(self, task_id: str, status: TaskStatus):
        """
        @summary Transition a task to a new status.
        @inputs task_id, status: TaskStatus enum
        @outputs none
        @side_effects updates database row
        """
        task = self.get_by_id(task_id)
        if task:
            task.status = status
