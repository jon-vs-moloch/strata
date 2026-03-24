"""
@module storage.repositories.parameters
@purpose High-level CRUD operations for the ParameterModel.
@owns orchestrator parameter storage, telemetry tracking, mutations
@does_not_own business logic orchestration, database connection lifecycle
@key_exports ParameterRepository
"""

from typing import Any, Optional
from sqlalchemy.orm import Session
from shotgun_tokens.storage.models import ParameterModel

class ParameterRepository:
    """
    @summary Manages dynamic evolutionary parameters in SQL.
    @inputs session: SQLAlchemy Session
    @outputs side-effect driven (DB mutations) or parameter values
    @side_effects writes to 'parameters' table
    @depends storage.models.ParameterModel
    @invariants does not commit the session.
    """
    def __init__(self, session: Session):
        self.session = session

    def get_parameter(self, key: str, default_value: Any, description: str = "") -> Any:
        """
        @summary Fetch a parameter value. If it doesn't exist, create it with the default.
        @inputs key: string identifier, default_value: initial value
        @outputs the current active value of the parameter
        """
        param = self.session.query(ParameterModel).filter_by(key=key).first()
        if not param:
            param = ParameterModel(
                key=key,
                description=description,
                value={"current": default_value, "history": []}
            )
            self.session.add(param)
            self.session.flush() # ensure it gets an ID but dont commit yet
        
        # Track usage
        param.usage_count += 1
        return param.value.get("current", default_value)

    def record_success(self, key: str):
        """
        @summary Record a successful outcome to reinforce the current parameter.
        """
        param = self.session.query(ParameterModel).filter_by(key=key).first()
        if param:
            param.success_count += 1

    def mutate_parameter(self, key: str, new_value: Any, rationale: str = ""):
        """
        @summary Update a parameter's value natively (e.g. proposed by an auto-maintenance job).
        """
        param = self.session.query(ParameterModel).filter_by(key=key).first()
        if param:
            old_value = param.value.get("current")
            history = param.value.get("history", [])
            history.append({
                "value": old_value,
                "usage_count": param.usage_count,
                "success_count": param.success_count,
                "rationale": rationale
            })
            
            param.value = {"current": new_value, "history": history}
            param.mutation_count += 1
            # Reset counters for the new evolutionary epoch
            param.usage_count = 0
            param.success_count = 0
