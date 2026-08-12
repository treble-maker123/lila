from __future__ import annotations

import statistics
from math import comb
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from src.memory import MemoryFootprint
from src.tokens import PromptProbe

# ``error`` is a *prediction-only* value: it means the setup never produced a
# routing decision (see ErrorKind). Gold labels in the datasets must never use it,
# so a failed run can never be scored as a correct route. In particular it is NOT
# a synonym for ``no_action`` — a setup that legitimately declines to act reports
# ``no_action``, and only pathological terminations report ``error``.
NextStep = Literal["reply", "no_action", "flag_for_human", "error"]
# The routes a setup may legitimately choose, and the only classes scored one-vs-rest.
# ``error`` is not among them: gold never says ``error``, so its tp and fn would be
# structurally 0 and its counts would just restate the error rate. Error *predictions*
# still land in these classes' counts — see ClassCounts.
ROUTING_CLASSES: tuple[str, ...] = ("reply", "no_action", "flag_for_human")
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
    # One or two sentences of situation — who these people are and what is going on
    # between them — so a reader can follow the email without reconstructing it from
    # the thread. Describes the setting, not the answer; that is ``note``.
    scenario: str = ""
    # Free-form human-annotated note about this data point. Blank ("") until
    # hand-written.
    note: str = ""
    # True when this email's ``get_note`` fixture is built to mislead — stale,
    # contradictory, or about a neighbouring matter. These are the emails where
    # gathering context costs accuracy instead of buying it, which is what keeps an
    # unconditional-gather setup from being free (see README "Dataset").
    notes_conflict: bool = False
    # Fixed return values for the environment tools, keyed by tool name. The mock
    # MCP server (see src/mcp_server.py) returns these verbatim so a run's tool
    # observations stay deterministic. Shape is tool-specific, e.g.
    # {"check_calendar_available": {"available": false}}. Empty when the email
    # needs no lookups.
    tool_returns: dict[str, Any] = Field(default_factory=dict)


class RunConfig(BaseModel):
    """Provider settings shared by every setup, so a setup can never differ in one."""

    model: str
    ollama_url: str
    temperature: float
    think: bool
    num_ctx: int
    # Graph only; None when the startup probe failed (see src/tokens.py).
    prompt_probe: PromptProbe | None = None


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
    the final call's prompt. The graph's two node prompts lead with the same email
    block by construction, so that block is subtracted once (see src/tokens.py).

    The gap between the two is the loop's re-reading overhead, which local KV-cache
    reuse largely eliminates in practice — so ``cumulative`` overstates the real cost
    of the loop on Ollama and ``unique`` understates it. Report both.
    """

    tokens_in_cumulative: int
    tokens_in_unique: int
    tokens_out: int
    wall_clock_ms: int
    # Read-tool calls on this email. Gathering is almost always +EV on this dataset,
    # so accuracy alone hides how much context each setup bought to get it. Routing
    # and get_new_email are excluded — one of each per email by construction.
    read_tool_calls: int = 0
    steps: int | None = None
    # Per-call prompt token counts, in call order, so the two totals above can be
    # re-derived and the provider's own caching behaviour audited.
    prompt_tokens: list[int] = Field(default_factory=list)
    # Most context the setup held at once on this email: max over provider calls of
    # (prompt + generated) tokens. This is the setup's KV high-water mark, and the
    # loop-vs-graph memory difference lives here — the loop re-sends a growing
    # transcript, the graph starts each node fresh.
    peak_context_tokens: int = 0
    # peak_context_tokens priced in bytes against the measured server (src/memory.py).
    # None when calibration failed; the token count above still stands.
    memory: MemoryFootprint | None = None


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


class ToolInvocation(BaseModel):
    name: str
    args: dict[str, Any]
    timestamp_ms: int


class RunResult(BaseModel):
    setup: int
    email_id: str
    predicted: Label
    metrics: Metrics
    # Set iff the setup produced no routing decision; then predicted.next_step is
    # "error". Cost metrics are still recorded (the tokens really were spent).
    error: RunError | None = None
    warnings: list[RunWarning] = Field(default_factory=list)
    # Every tool call in order, so read_tool_calls can be audited rather than trusted.
    invocations: list[ToolInvocation] = Field(default_factory=list)
    debug: Debug = Field(default_factory=Debug)


class InferenceResult(BaseModel):
    label: Label
    # Per-call prompt token counts in call order; the two input-token totals in
    # Metrics are derived from this by each setup (see Metrics).
    prompt_tokens: list[int] = Field(default_factory=list)
    tokens_in_cumulative: int
    tokens_in_unique: int
    tokens_out: int
    # Max over provider calls of (prompt + generated) tokens; see Metrics.
    peak_context_tokens: int = 0
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
    read_tool_calls: int = 0
    errors: int
    # Memory aggregates upward as a max, not a sum: emails run one after another, so
    # what a setup needs is its worst email, not the total across them.
    peak_context_tokens: int = 0
    memory: MemoryFootprint | None = None


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
    read_tool_calls: int = 0
    # Email-runs that produced no routing decision (predicted.next_step == "error").
    errors: int
    # High-water mark across every email of every run of this setup.
    peak_context_tokens: int = 0
    memory: MemoryFootprint | None = None


class Distribution(BaseModel):
    """A per-run count, stored as the raw per-run values.

    ``values`` is the stored truth — the harness emits counts, not ratios (see README
    "Metrics"), and keeping the per-run values is what lets runs be re-aggregated
    later. The summary statistics are computed from them rather than stored, but do
    serialize, so a results file can be read without recomputing anything.
    """

    values: list[int] = Field(default_factory=list)

    @computed_field
    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self.values else 0.0

    @computed_field
    @property
    def minimum(self) -> int:
        return min(self.values, default=0)

    @computed_field
    @property
    def maximum(self) -> int:
        return max(self.values, default=0)

    @computed_field
    @property
    def stdev(self) -> float:
        """Sample stdev; 0.0 for a single run, which has no spread to report."""
        return statistics.stdev(self.values) if len(self.values) > 1 else 0.0


class ClassCounts(BaseModel):
    """One-vs-rest counts for a single routing class, summed over runs.

    An ``error`` prediction is counted against every class: ``fn`` for the class the
    label owed, ``fp`` for the others. It is never a ``tn``, which would credit a setup
    for failing to decide on an email it was never going to get right anyway. Under
    this rule each email-run contributes exactly one cell, so tp+fp+fn+tn == email-runs
    for every class.
    """

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @computed_field
    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @computed_field
    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @computed_field
    @property
    def f1(self) -> float:
        denom = self.precision + self.recall
        return 2 * self.precision * self.recall / denom if denom else 0.0


class ErrorBreakdown(BaseModel):
    """Email-runs that produced no routing decision, split by ErrorKind."""

    total: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)


class PassPoint(BaseModel):
    """One point of the pass^k curve: the share of emails answered correctly by all
    k runs of a randomly chosen k of the n that were performed."""

    k: int
    value: float


def pass_hat_k(successes: list[int], runs: int, k: int) -> float:
    """Share of emails that a randomly chosen k of the ``runs`` trials all get right.

        pass^k = (1/emails) * sum_i C(c_i, k) / C(runs, k)

    Unbiased in the number of trials, unlike sampling k of them directly. C(c, k) is 0
    when c < k, so an email answered correctly fewer than k times contributes nothing —
    pass^k is all-or-nothing per email, which is the point.
    """
    if not successes or not 1 <= k <= runs:
        return 0.0
    return sum(comb(c, k) for c in successes) / (comb(runs, k) * len(successes))


class LabelMetrics(BaseModel):
    """Routing accuracy for one setup across every run of it (README "Labels")."""

    setup: int
    label: str
    runs: int
    emails: int
    # Per run, over all emails. An ``error`` is never correct.
    correct: Distribution
    # Per run: emails - errors. Denominator for routing quality with robustness out.
    decided: Distribution
    # Per email, whether the most common answer across runs matches the label. A tie
    # is no consensus and so never correct.
    majority_correct: int
    # Per email, how many runs matched the label. The whole pass^k curve derives from
    # these counts, so no k needs to be chosen at scoring time.
    successes: dict[str, int] = Field(default_factory=dict)
    by_next_step: dict[str, ClassCounts] = Field(default_factory=dict)
    # Same counts sliced by Email.category, showing which email shapes break.
    by_category: dict[str, dict[str, ClassCounts]] = Field(default_factory=dict)
    errors: ErrorBreakdown = Field(default_factory=ErrorBreakdown)

    @computed_field
    @property
    def email_runs(self) -> int:
        return self.emails * self.runs

    @computed_field
    @property
    def error_rate(self) -> float:
        return self.errors.total / self.email_runs if self.email_runs else 0.0

    @computed_field
    @property
    def pass_curve(self) -> list[PassPoint]:
        """pass^k for k = 1…runs. pass^1 equals the mean of correct/emails."""
        counts = list(self.successes.values())
        return [
            PassPoint(k=k, value=pass_hat_k(counts, self.runs, k)) for k in range(1, self.runs + 1)
        ]
