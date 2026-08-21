"""Setup 1: ReAct loop, local 9B via Ollama."""

from __future__ import annotations

import json

import ollama

from loop_workflow_comparison.mcp_server import TOOLS, MockMCPServer, UnknownToolError, call_role
from loop_workflow_comparison.models import (
    DEFAULT_DRAFT,
    Action,
    Debug,
    Email,
    InferenceResult,
    Label,
    LoopDebug,
    RunConfig,
    RunError,
    RunWarning,
)
from loop_workflow_comparison.prompts import (
    EMAIL_TRIAGE_SKILL,
    GENERIC_AGENT_SYSTEM,
    render_tool_result,
)
from loop_workflow_comparison.tokens import split_output

MAX_STEPS = 12

# Generic agent loop + the triage skill (the declarative peer of setup 2's graph).
_SYSTEM = f"{GENERIC_AGENT_SYSTEM}\n\n{EMAIL_TRIAGE_SKILL}"


def parse_actions(raw: object, warnings: list[RunWarning]) -> list[Action]:
    """Parse model-supplied action items, dropping the ones that don't fit the schema.

    Deliberately lenient: a malformed action item degrades the actions list but does
    not invalidate the routing decision, which is the primary metric. Each drop is
    recorded as a warning so it stays visible instead of being silently swallowed.
    """
    if not isinstance(raw, list):
        if raw not in (None, ""):
            warnings.append(
                RunWarning(kind="action_parse_error", detail=f"actions not a list: {raw!r}")
            )
        return []
    actions: list[Action] = []
    for item in raw:
        if not isinstance(item, dict):
            warnings.append(
                RunWarning(kind="action_parse_error", detail=f"action not an object: {item!r}")
            )
            continue
        verb = item.get("verb")
        subject = item.get("subject")
        if not isinstance(verb, str) or not isinstance(subject, str):
            warnings.append(
                RunWarning(
                    kind="action_parse_error", detail=f"action missing verb/subject: {item!r}"
                )
            )
            continue
        deadline = item.get("deadline")
        actions.append(
            Action(
                verb=verb,
                subject=subject,
                deadline=deadline if isinstance(deadline, str) else None,
            )
        )
    return actions


def run_email(email: Email, cfg: RunConfig) -> InferenceResult:
    client = ollama.Client(host=cfg.ollama_url)
    server = MockMCPServer(email)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "Process the next email in the inbox."},
    ]

    state: dict[str, object] = {"actions": [], "next_step": None, "draft": None}
    prompt_tokens: list[int] = []
    call_roles: list[str] = []
    tokens_out = 0
    tokens_out_pre = 0
    tokens_out_post = 0
    split_exact = True
    # KV high-water mark. The last call of a growing conversation is normally the
    # largest, but take the max rather than the last so a truncated or failed final
    # call cannot understate what the loop actually held.
    peak_context_tokens = 0
    steps = 0
    loops: list[LoopDebug] = []
    warnings: list[RunWarning] = []
    error: RunError | None = None
    fetched_email = False
    # True only if the loop ran out of steps while the model still wanted to act.
    exhausted = False

    for step in range(MAX_STEPS):
        steps = step + 1
        try:
            resp = client.chat(
                model=cfg.model,
                messages=messages,
                tools=TOOLS,
                think=cfg.think,
                options={"temperature": cfg.temperature, "num_ctx": cfg.num_ctx},
            )
        except Exception as exc:  # transport/decode/timeout from the backend
            error = RunError(kind="provider_error", detail=f"{type(exc).__name__}: {exc}")
            break
        msg = resp.message
        prompt_tokens.append(resp.prompt_eval_count or 0)
        tokens_out += resp.eval_count or 0
        peak_context_tokens = max(
            peak_context_tokens, (resp.prompt_eval_count or 0) + (resp.eval_count or 0)
        )
        # The loop's roles are inferred from what it chose to call; the graph's are its
        # nodes. That asymmetry is the thing being measured, not a measurement flaw.
        raw_calls = [
            {"name": tc.function.name, "arguments": dict(tc.function.arguments or {})}
            for tc in (msg.tool_calls or [])
        ]
        call_roles.append(call_role([c["name"] for c in raw_calls]))
        pre, post, exact = split_output(
            resp.eval_count or 0, msg.content or "", json.dumps(raw_calls) if raw_calls else ""
        )
        tokens_out_pre += pre
        tokens_out_post += post
        split_exact = split_exact and exact
        loops.append(
            LoopDebug(
                input=[dict(m) for m in messages],
                output=msg.model_dump(),
                thinking=getattr(msg, "thinking", None) if cfg.think else None,
            )
        )
        messages.append(msg.model_dump())

        # No tool call means the model stopped talking. Since no_action is now an
        # explicit tool, stopping without routing is no longer how no-action is
        # expressed — it is a failure to decide (see no_route_called below).
        if not msg.tool_calls:
            break

        done = False
        for tc in msg.tool_calls:
            name = tc.function.name
            args: dict[str, object] = tc.function.arguments or {}
            if name == "reply":
                state["next_step"] = "reply"
                state["draft"] = args.get("message")
                done = True
            elif name == "no_action":
                state["next_step"] = "no_action"
                done = True
            elif name == "flag_for_human":
                state["next_step"] = "flag_for_human"
                state["actions"] = parse_actions(args.get("actions"), warnings)
                done = True
            try:
                result = server.handle(name, args)
            except UnknownToolError as exc:
                error = RunError(kind="unknown_tool", detail=str(exc))
                done = True
                break
            if name == "get_new_email":
                fetched_email = True
            messages.append(
                {"role": "tool", "content": render_tool_result(name, result), "name": name}
            )
            # First routing call wins, matching the graph's decide node. Without the
            # break a message holding two routing calls scored its *last* one here and
            # its *first* one there, so the same output could score differently by setup.
            if done:
                break
        # Routing is terminal.
        if done:
            break
    else:
        # Fell out of the loop still holding an unserviced tool call.
        exhausted = True

    # Resolve the route. Order matters: a real failure must never be reported as a
    # deliberate no_action, because no_action is a correct answer for ~22% of the
    # dataset and would otherwise earn credit for flailing.
    if error is not None:
        next_step = "error"
    elif exhausted:
        error = RunError(
            kind="max_steps_exhausted", detail=f"still calling tools after {MAX_STEPS} steps"
        )
        next_step = "error"
    elif state["next_step"] is not None:
        next_step = str(state["next_step"])
    elif not fetched_email:
        # Stopped before even reading the email; it cannot have decided anything.
        error = RunError(kind="no_email_fetched", detail="terminated without calling get_new_email")
        next_step = "error"
    else:
        error = RunError(kind="no_route_called", detail="stopped without calling a routing tool")
        next_step = "error"

    label = Label(
        actions=state["actions"] if next_step == "flag_for_human" else [],  # type: ignore[arg-type]
        next_step=next_step,  # type: ignore[arg-type]
        draft=str(state["draft"]) if next_step == "reply" and state["draft"] else DEFAULT_DRAFT,
    )
    return InferenceResult(
        label=label,
        prompt_tokens=prompt_tokens,
        tokens_in_cumulative=sum(prompt_tokens),
        # The message list only ever grows, so every earlier prompt is a prefix of
        # the last one and the final prompt is the count of distinct input tokens.
        tokens_in_unique=prompt_tokens[-1] if prompt_tokens else 0,
        tokens_out=tokens_out,
        call_roles=call_roles,  # type: ignore[arg-type]
        tokens_out_pre=tokens_out_pre,
        tokens_out_post=tokens_out_post,
        tokens_out_split="exact" if split_exact else "estimated",
        peak_context_tokens=peak_context_tokens,
        steps=steps,
        error=error,
        warnings=warnings,
        invocations=server.invocations,
        debug=Debug(loops=loops),
    )
