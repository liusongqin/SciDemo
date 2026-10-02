from __future__ import annotations
from typing import Any, Literal, TypedDict
from pydantic import BaseModel, Field

class ScientificAgentState(TypedDict, total=False):
    task_id: str
    conversation_id: str
    conversation_history: list[dict[str, Any]]
    conversation_summary: str
    conversation_task_ids: list[str]
    workflow_kind: str
    user_query: str
    parsed_problem: dict[str, Any]
    computation_mode: str
    plan: list[dict[str, Any]]
    current_step: int
    tool_calls: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    model_calls: list[dict[str, Any]]
    model_provider: str
    agent_steps: list[dict[str, Any]]
    active_agent: str
    agent_handoffs: list[dict[str, Any]]
    pending_action: dict[str, Any] | None
    review_completed: bool
    use_local_model: bool
    status: str
    requires_human_review: bool
    human_feedback: dict[str, Any] | None
    retry_count: int
    max_retries: int
    tolerance: float
    teaching_mode: bool
    final_answer: str | None
    error: str | None

class TaskCreate(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    teaching_mode: bool = False
    require_review: bool = False
    tolerance: float = Field(default=1e-8, gt=0, le=0.1)
    max_retries: int = Field(default=2, ge=0, le=5)
    use_local_model: bool = True
    model_backend: Literal["local", "external", "mock"] = "local"
    parent_task_id: str | None = None
    conversation_id: str | None = None

class DirectLoginRequest(BaseModel):
    prelogin_id: str = Field(min_length=16,max_length=128)
    student_id: str = Field(min_length=4,max_length=32,pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=1,max_length=256)
    captcha: str = Field(default="",max_length=32)

class ConversationRename(BaseModel):
    title: str = Field(min_length=1,max_length=60)

class HumanFeedback(BaseModel):
    action: Literal["approve", "modify", "reject"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    comment: str = Field(default="", max_length=500)

class WorkflowEvent(BaseModel):
    task_id: str
    sequence: int
    timestamp: str
    event_type: str
    node: str | None = None
    title: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = None
