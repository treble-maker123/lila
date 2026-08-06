from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Classification = Literal["action_required", "fyi", "promotional", "suspicious", "don't know"]
NextStep = Literal["reply", "no_action", "flag_for_human"]
# Generation-time bucket the email was scaffolded under (see datasets/scripts/generate_individual.py).
# Blank ("") for hand-written emails that were not scaffolded.
Category = Literal["promotional", "fyi", "single-ask", "multi-ask", "buried", "suspicious", ""]
# Expected values are easy/medium/hard; left blank ("") until hand-labeled.
Difficulty = Literal["easy", "medium", "hard", ""]

# Shared defaults used when a setup cannot produce a value.
DEFAULT_CLASSIFICATION: Classification = "don't know"
DEFAULT_NEXT_STEP: NextStep = "no_action"
DEFAULT_DRAFT: str | None = None


class Action(BaseModel):
    verb: str
    subject: str
    deadline: str | None = None


class Label(BaseModel):
    classification: Classification = DEFAULT_CLASSIFICATION
    actions: list[Action] = Field(default_factory=list)
    next_step: NextStep = DEFAULT_NEXT_STEP
    draft: str | None = DEFAULT_DRAFT


class Headers(BaseModel):
    """RFC-822-style envelope for an email. ``from_`` serializes as ``from``
    (a Python keyword) via its alias."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(default="", alias="from")
    to: str = ""
    cc: str = ""
    date: str = ""
    subject: str = ""


class Email(BaseModel):
    id: str
    headers: Headers
    body: str
    label: Label
    category: Category = ""
    difficulty: Difficulty = ""
    # Free-form human-annotated note about this data point. Blank ("") until
    # hand-written.
    note: str = ""
    # Fixed return values for the environment tools, keyed by tool name. The mock
    # MCP server (see src/mcp_server.py) returns these verbatim so a run's tool
    # observations stay deterministic. Shape is tool-specific, e.g.
    # {"check_calendar_available": {"available": false}}. Empty when the email
    # needs no lookups.
    tool_returns: dict[str, Any] = Field(default_factory=dict)


class Metrics(BaseModel):
    # Accuracy was intentionally dropped; scoring is done separately/later against
    # the captured outputs. These are cost metrics only.
    tokens_in: int
    tokens_out: int
    wall_clock_ms: int
    steps: int | None = None


class LoopDebug(BaseModel):
    """Debug record for a single ReAct loop iteration."""

    input: Any
    output: Any
    thinking: str | None = None


class NodeDebug(BaseModel):
    """Debug record for a single graph-workflow node."""

    node: str
    input: Any
    output: Any
    parameters: dict[str, Any] = Field(default_factory=dict)
    thinking: str | None = None


class Debug(BaseModel):
    loops: list[LoopDebug] = Field(default_factory=list)
    nodes: list[NodeDebug] = Field(default_factory=list)


class RunResult(BaseModel):
    setup: int
    email_id: str
    predicted: Label
    metrics: Metrics
    debug: Debug = Field(default_factory=Debug)


class ToolInvocation(BaseModel):
    name: str
    args: dict[str, Any]
    timestamp_ms: int


class InferenceResult(BaseModel):
    label: Label
    tokens_in: int
    tokens_out: int
    steps: int = 0
    invocations: list[ToolInvocation] = []
    debug: Debug = Field(default_factory=Debug)


class VotedInferenceResult(BaseModel):
    label: Label
    tokens_in: int
    tokens_out: int
    steps: int
    agreement: float
    invocations: list[list[ToolInvocation]] = []
    debug: Debug = Field(default_factory=Debug)


class MetricsSummary(BaseModel):
    total: int
    tokens_in: int
    tokens_out: int
    wall_clock_ms: int


class SetupSummary(BaseModel):
    """One row of the results table, aggregated across all runs of a setup."""

    setup: int
    label: str
    runs: int
    emails: int
    tokens_in: int
    tokens_out: int
    wall_clock_ms: int
