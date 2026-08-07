from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ``error`` is a *prediction-only* value: it means the setup never produced a
# routing decision (see ErrorKind). Gold labels in the datasets must never use it,
# so a failed run can never be scored as a correct route. In particular it is NOT
# a synonym for ``no_action`` — a setup that legitimately declines to act reports
# ``no_action``, and only pathological terminations report ``error``.
NextStep = Literal["reply", "no_action", "flag_for_human", "error"]
# Generation-time bucket the email was scaffolded under (see datasets/scripts/generate_individual.py).
# Blank ("") for hand-written emails that were not scaffolded.
Category = Literal["promotional", "fyi", "single-ask", "multi-ask", "buried", "suspicious", ""]
# Expected values are easy/medium/hard; left blank ("") until hand-labeled.
Difficulty = Literal["easy", "medium", "hard", ""]

# Shared defaults used when a setup cannot produce a value.
DEFAULT_NEXT_STEP: NextStep = "no_action"
DEFAULT_DRAFT: str | None = None

# Terminations that mean "the setup produced no routing decision", shared by every
# setup so failures are counted the same way regardless of mechanism. Each setup
# maps its own mechanism's pathologies onto these:
#
#   max_steps_exhausted  loop hit MAX_STEPS still wanting to call tools (loop only)
#   no_email_fetched     terminated without ever calling get_new_email (loop only;
#                        the graph's control flow always fetches first)
#   no_route_called      terminated without calling reply/no_action/flag_for_human.
#                        Since no_action is an explicit tool, silence is a failure to
#                        decide, not a decision.
#   unknown_tool         called a tool the mock server has no fixture for
#   provider_error       the model backend raised (transport, decode, timeout)
ErrorKind = Literal[
    "max_steps_exhausted",
    "no_email_fetched",
    "no_route_called",
    "unknown_tool",
    "provider_error",
]

# Degradations that do NOT invalidate the routing decision, so the email still
# scores. Recorded to keep them visible instead of silently swallowed.
#
#   action_parse_error   an action item was malformed and was dropped
#   out_of_node_tool     the graph's model called a tool outside the current node's
#                        set; the call is skipped and the node continues
WarningKind = Literal["action_parse_error", "out_of_node_tool"]


class RunError(BaseModel):
    kind: ErrorKind
    detail: str


class RunWarning(BaseModel):
    kind: WarningKind
    detail: str


class Action(BaseModel):
    verb: str
    subject: str
    deadline: str | None = None


class Label(BaseModel):
    # No ``classification`` field: email type is dataset metadata (Email.category),
    # not something the agent emits. See README "Task".
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
    """Cost metrics for one email. Accuracy is intentionally absent; scoring is done
    separately/later against the captured outputs.

    Input tokens are reported two ways because the loop and the graph spend them
    very differently, and reporting only one number makes the comparison misleading:

    ``tokens_in_cumulative`` sums the prompt of every provider call. A ReAct loop
    re-sends its whole conversation each turn, so an N-turn loop counts the shared
    prefix N times. This is what an uncached, per-call-billed API charges.

    ``tokens_in_unique`` counts each distinct prompt token once — what a perfect
    prefix cache would have to evaluate. For a loop the message list only ever grows,
    so every earlier prompt is a prefix of the final one and the unique total is just
    the final call's prompt. For the graph the nodes are independent prompts that
    share no usable prefix, so unique == cumulative.

    The gap between the two is the loop's re-reading overhead, which local KV-cache
    reuse largely eliminates in practice — so ``cumulative`` overstates the real cost
    of the loop on Ollama and ``unique`` understates it. Report both.
    """

    tokens_in_cumulative: int
    tokens_in_unique: int
    tokens_out: int
    wall_clock_ms: int
    steps: int | None = None
    # Per-call prompt token counts, in call order, so the two totals above can be
    # re-derived and the provider's own caching behaviour audited.
    prompt_tokens: list[int] = Field(default_factory=list)


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
    # Set iff the setup produced no routing decision; then predicted.next_step is
    # "error". Cost metrics are still recorded (the tokens really were spent).
    error: RunError | None = None
    warnings: list[RunWarning] = Field(default_factory=list)
    debug: Debug = Field(default_factory=Debug)


class ToolInvocation(BaseModel):
    name: str
    args: dict[str, Any]
    timestamp_ms: int


class InferenceResult(BaseModel):
    label: Label
    # Per-call prompt token counts in call order; the two input-token totals in
    # Metrics are derived from this by each setup (see Metrics).
    prompt_tokens: list[int] = Field(default_factory=list)
    tokens_in_cumulative: int
    tokens_in_unique: int
    tokens_out: int
    steps: int = 0
    error: RunError | None = None
    warnings: list[RunWarning] = Field(default_factory=list)
    invocations: list[ToolInvocation] = []
    debug: Debug = Field(default_factory=Debug)


class MetricsSummary(BaseModel):
    total: int
    tokens_in_cumulative: int
    tokens_in_unique: int
    tokens_out: int
    wall_clock_ms: int
    errors: int


class SetupSummary(BaseModel):
    """One row of the results table, aggregated across all runs of a setup."""

    setup: int
    label: str
    runs: int
    emails: int
    tokens_in_cumulative: int
    tokens_in_unique: int
    tokens_out: int
    wall_clock_ms: int
    # Email-runs that produced no routing decision (predicted.next_step == "error").
    errors: int
