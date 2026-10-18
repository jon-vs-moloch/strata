"""
@module orchestrator.step_outcomes
@purpose Shared step-level terminal outcomes used by orchestrator task runners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TerminalToolCallOutcome:
    """
    Terminal outcome for a step that ended by emitting and executing one tool call.

    The orchestrator owns what happens next: it may stop, hand off deterministic
    fallout to another explicit step, or branch according to task constraints.
    """

    tool_name: str
    tool_arguments: Dict[str, Any] = field(default_factory=dict)
    tool_result_preview: str = ""
    tool_result_full: str = ""
    next_step_hint: str = ""
    source_module: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    continuation_title: Optional[str] = None
    continuation_description: Optional[str] = None
    continuation_task_type: Optional[str] = None
    continuation_constraints: Dict[str, Any] = field(default_factory=dict)

