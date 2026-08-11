"""Setup 2: Fixed graph workflow, local 9B via Ollama.

Same tools/environment and the same native tool-calling interface as the ReAct
setup; only the control flow differs:

    get_new_email -> gather_context -> decide (reply | no_action | flag_for_human)

Each node is a single turn with just the tools that node may use. The model never
chooses what comes next, and there is no agent-loop system prompt — knowing the
node's job up front is what the graph buys. Those are the two remaining
differences from setup 1, which is what a 1-vs-2 delta attributes to.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import ollama
from pydantic import BaseModel

from src.mcp_server import READ_TOOLS, ROUTE_TOOLS, MockMCPServer, UnknownToolError, tools_for
from src.memory import KVProfile
from src.models import (
    DEFAULT_DRAFT,
    Action,
    Debug,
    Email,
    InferenceResult,
    Label,
    Metrics,
    NodeDebug,
    RunError,
    RunResult,
    RunWarning,
)
from src.prompts import GATHER_POLICY, ROUTING_INSTRUCTION, ROUTING_POLICY, render_tool_result
from src.setup1_react_local import parse_actions


class _ToolCall(BaseModel):
    name: str
    args: dict[str, Any]


class _NodeResponse(BaseModel):
    calls: list[_ToolCall]
    content: str
    thinking: str | None
    tokens_in: int
    tokens_out: int


class _ProviderError(Exception):
    """The Ollama backend raised; the caller turns this into ErrorKind provider_error."""


def _chat_tools(
    client: ollama.Client,
    model: str,
    temperature: float,
    think: bool,
    num_ctx: int,
    prompt: str,
    tool_names: set[str],
) -> _NodeResponse:
    """One node turn: prompt the model with this node's tools and read back its calls."""
    try:
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            tools=tools_for(tool_names),
            think=think,
            options={"temperature": temperature, "num_ctx": num_ctx},
        )
    except Exception as exc:
        raise _ProviderError(f"{type(exc).__name__}: {exc}") from exc
    calls = [
        _ToolCall(name=tc.function.name, args=dict(tc.function.arguments or {}))
        for tc in (resp.message.tool_calls or [])
    ]
    return _NodeResponse(
        calls=calls,
        content=resp.message.content or "",
        thinking=getattr(resp.message, "thinking", None) if think else None,
        tokens_in=resp.prompt_eval_count or 0,
        tokens_out=resp.eval_count or 0,
    )


def run_email(
    email: Email, model: str, ollama_url: str, temperature: float, think: bool, num_ctx: int
) -> InferenceResult:
    client = ollama.Client(host=ollama_url)
    server = MockMCPServer(email)
    prompt_tokens: list[int] = []
    tokens_out = 0
    # KV high-water mark. Nodes are independent prompts, so the peak is the largest
    # single node, not the sum — that is the whole memory argument for the graph.
    peak_context_tokens = 0
    nodes: list[NodeDebug] = []
    warnings: list[RunWarning] = []

    def failed(error: RunError) -> InferenceResult:
        """Abandon this email with no routing decision, keeping the cost incurred."""
        return InferenceResult(
            label=Label(next_step="error"),
            prompt_tokens=prompt_tokens,
            tokens_in_cumulative=sum(prompt_tokens),
            tokens_in_unique=sum(prompt_tokens),
            tokens_out=tokens_out,
            peak_context_tokens=peak_context_tokens,
            error=error,
            warnings=warnings,
            invocations=server.invocations,
            debug=Debug(nodes=nodes),
        )

    # Node 1: get_new_email (fetched through the server, like the loop setup).
    email_text = server.handle("get_new_email", {})["email"]
    nodes.append(NodeDebug(node="get_new_email", input="", output=email_text, parameters={}))

    # Node 2: gather_context — the model requests lookups, the workflow dispatches them.
    # Both node prompts lead with the identical email block so the second node's
    # prefill reuses the first node's KV cache. Node-specific instructions go last;
    # anything that varies between nodes must stay after the shared prefix or the
    # cache match breaks at the first differing token.
    gather_prompt = f"{email_text}\n\nBefore deciding how to handle this email:\n{GATHER_POLICY}"
    try:
        gathered = _chat_tools(
            client, model, temperature, think, num_ctx, gather_prompt, READ_TOOLS
        )
    except _ProviderError as exc:
        return failed(RunError(kind="provider_error", detail=f"gather_context: {exc}"))
    prompt_tokens.append(gathered.tokens_in)
    tokens_out += gathered.tokens_out
    peak_context_tokens = max(peak_context_tokens, gathered.tokens_in + gathered.tokens_out)

    observations: list[dict[str, Any]] = []
    for call in gathered.calls:
        # A tool outside this node's set is a degradation, not a failure: the decide
        # node still runs, just with less context.
        if call.name not in READ_TOOLS:
            warnings.append(
                RunWarning(kind="out_of_node_tool", detail=f"gather_context called {call.name}")
            )
            continue
        try:
            result = server.handle(call.name, call.args)
        except UnknownToolError as exc:
            return failed(RunError(kind="unknown_tool", detail=str(exc)))
        observations.append({"tool": call.name, "args": call.args, "result": result})
    nodes.append(
        NodeDebug(
            node="gather_context",
            input=gather_prompt,
            output=gathered.content,
            parameters={"observations": observations},
            thinking=gathered.thinking,
        )
    )

    # Node 3: decide — route via exactly one routing tool. Actions are only extracted
    # here, on the flag_for_human path.
    obs_text = (
        "\n".join(
            f"- {o['tool']}({o['args']}) -> {render_tool_result(o['tool'], o['result'])}"
            for o in observations
        )
        or "(none)"
    )
    decide_prompt = (
        f"{email_text}\n\n"
        "Decide how to handle this email, using these definitions:\n"
        f"{ROUTING_POLICY}"
        f"{ROUTING_INSTRUCTION}"
        f"\nContext you gathered:\n{obs_text}"
    )
    try:
        decided = _chat_tools(
            client, model, temperature, think, num_ctx, decide_prompt, ROUTE_TOOLS
        )
    except _ProviderError as exc:
        return failed(RunError(kind="provider_error", detail=f"decide: {exc}"))
    prompt_tokens.append(decided.tokens_in)
    tokens_out += decided.tokens_out
    peak_context_tokens = max(peak_context_tokens, decided.tokens_in + decided.tokens_out)

    draft: str | None = None
    actions: list[Action] = []
    next_step: str | None = None
    for call in decided.calls:
        if call.name not in ROUTE_TOOLS:
            warnings.append(
                RunWarning(kind="out_of_node_tool", detail=f"decide called {call.name}")
            )
            continue
        if call.name == "reply":
            message = call.args.get("message")
            draft = str(message).strip() or None if message else None
            next_step = "reply"
        elif call.name == "no_action":
            next_step = "no_action"
        else:
            actions = parse_actions(call.args.get("actions"), warnings)
            next_step = "flag_for_human"
        try:
            server.handle(call.name, call.args)
        except UnknownToolError as exc:
            return failed(RunError(kind="unknown_tool", detail=str(exc)))
        # First routing call wins, matching the loop's terminal-decision behaviour.
        break

    nodes.append(
        NodeDebug(
            node="decide",
            input=decide_prompt,
            output=decided.content,
            parameters={
                "route": next_step,
                "draft": draft,
                "actions": [a.model_dump() for a in actions],
            },
            thinking=decided.thinking,
        )
    )

    # no_action is an explicit tool now, so calling nothing is a failure to decide
    # rather than a silent no_action.
    if next_step is None:
        return failed(RunError(kind="no_route_called", detail="decide called no routing tool"))

    return InferenceResult(
        label=Label(
            actions=actions,
            next_step=next_step,  # type: ignore[arg-type]
            draft=draft if next_step == "reply" else DEFAULT_DRAFT,
        ),
        prompt_tokens=prompt_tokens,
        tokens_in_cumulative=sum(prompt_tokens),
        # Independent node prompts share no cacheable prefix, so there is nothing to
        # deduplicate: unique == cumulative.
        tokens_in_unique=sum(prompt_tokens),
        tokens_out=tokens_out,
        peak_context_tokens=peak_context_tokens,
        warnings=warnings,
        invocations=server.invocations,
        debug=Debug(nodes=nodes),
    )


def run(
    emails: list[Email],
    model: str,
    ollama_url: str,
    temperature: float,
    think: bool,
    num_ctx: int,
    profile: KVProfile | None,
    on_result: Callable[[RunResult], None] | None = None,
) -> list[RunResult]:
    results: list[RunResult] = []
    for email in emails:
        if not email.body.strip():
            print(f"[setup2] {email.id}: skipped empty email")
            continue
        t0 = time.monotonic()
        inferred = run_email(email, model, ollama_url, temperature, think, num_ctx)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if inferred.error:
            print(f"[setup2] {email.id}: [{inferred.error.kind}]")
        result = RunResult(
            setup=2,
            email_id=email.id,
            predicted=inferred.label,
            metrics=Metrics(
                tokens_in_cumulative=inferred.tokens_in_cumulative,
                tokens_in_unique=inferred.tokens_in_unique,
                tokens_out=inferred.tokens_out,
                wall_clock_ms=elapsed_ms,
                prompt_tokens=inferred.prompt_tokens,
                peak_context_tokens=inferred.peak_context_tokens,
                memory=profile.footprint(inferred.peak_context_tokens) if profile else None,
            ),
            error=inferred.error,
            warnings=inferred.warnings,
            debug=inferred.debug,
        )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
