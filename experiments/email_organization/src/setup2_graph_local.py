"""Setup 2: Fixed graph workflow, local 9B via Ollama.

Same tools/environment as the ReAct setups, but the control flow is fixed instead
of model-driven:

    get_new_email -> gather_context -> decide (reply | flag_for_human | none)

Every tool call goes through the mock MCP server, so tool invocations are recorded
the same way as the loop setups; the model only fills in each node's arguments.
"""

from __future__ import annotations

import json
import time

import ollama
from pydantic import BaseModel

from src.mcp_server import MockMCPServer
from src.models import (
    DEFAULT_DRAFT,
    DEFAULT_NEXT_STEP,
    Action,
    Debug,
    Email,
    InferenceResult,
    Label,
    Metrics,
    NodeDebug,
    RunResult,
)
from src.prompts import ROUTING_POLICY

# Read tools the gather_context node is allowed to dispatch.
_READ_TOOLS = {"check_calendar_available", "check_unknown_sender", "get_note"}


class _JsonResponse(BaseModel):
    data: dict
    content: str
    thinking: str | None
    tokens_in: int
    tokens_out: int


def _chat_json(
    client: ollama.Client, model: str, temperature: float, think: bool, prompt: str
) -> _JsonResponse:
    resp = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        think=think,
        options={"temperature": temperature},
        format="json",
    )
    try:
        data = json.loads(resp.message.content)
    except json.JSONDecodeError:
        data = {}
    return _JsonResponse(
        data=data,
        content=resp.message.content or "",
        thinking=getattr(resp.message, "thinking", None) if think else None,
        tokens_in=resp.prompt_eval_count or 0,
        tokens_out=resp.eval_count or 0,
    )


def run_email(
    email: Email, model: str, ollama_url: str, temperature: float, think: bool
) -> InferenceResult:
    client = ollama.Client(host=ollama_url)
    server = MockMCPServer(email)
    tokens_in = tokens_out = 0
    nodes: list[NodeDebug] = []

    # Node 1: get_new_email (fetched through the server, like the loop setups).
    email_text = server.handle("get_new_email", {})["email"]
    nodes.append(NodeDebug(node="get_new_email", input="", output=email_text, parameters={}))

    # Node 2: gather_context — the model requests lookups, the workflow dispatches them.
    gather_prompt = (
        "Before deciding how to handle this email, list any lookups you need. Available "
        'tools: check_calendar_available(time, length) -> {"available": bool}, '
        'check_unknown_sender(sender) -> {"known": bool}, '
        'get_note(key) -> {"note": text or null}.\n'
        "Respond with JSON (empty list if none): "
        '{"lookups": [{"tool": "<name>", "args": {...}}]}\n\n'
        f"{email_text}"
    )
    r3 = _chat_json(client, model, temperature, think, gather_prompt)
    tokens_in += r3.tokens_in
    tokens_out += r3.tokens_out
    observations = []
    for lookup in r3.data.get("lookups") or []:
        if not isinstance(lookup, dict) or lookup.get("tool") not in _READ_TOOLS:
            continue
        args = lookup.get("args") or {}
        result = server.handle(lookup["tool"], args)
        observations.append({"tool": lookup["tool"], "args": args, "result": result})
    nodes.append(
        NodeDebug(
            node="gather_context",
            input=gather_prompt,
            output=r3.content,
            parameters={"observations": observations},
            thinking=r3.thinking,
        )
    )

    # Node 3: decide — route to reply / flag_for_human / none. Actions are only
    # extracted here, on the flag_for_human path.
    obs_text = (
        "\n".join(f"- {o['tool']}({o['args']}) -> {o['result']}" for o in observations) or "(none)"
    )
    decide_prompt = (
        "Decide how to handle this email. Choose exactly one route using these "
        'definitions (no action corresponds to route "none"):\n'
        f"{ROUTING_POLICY}"
        f"\nContext you gathered:\n{obs_text}\n\n"
        'Respond with JSON: {"route": "reply"|"flag_for_human"|"none", '
        '"message": "<reply text, only if route is reply>", '
        '"actions": [{"verb": ..., "subject": ..., "deadline": ...}] '
        "(only if route is flag_for_human, the items needing attention)}\n\n"
        f"{email_text}"
    )
    r3d = _chat_json(client, model, temperature, think, decide_prompt)
    tokens_in += r3d.tokens_in
    tokens_out += r3d.tokens_out
    route = r3d.data.get("route", "none")
    draft = None
    actions: list[Action] = []
    if route == "reply":
        draft = (r3d.data.get("message") or "").strip() or None
        server.handle("reply", {"message": draft or ""})
        next_step = "reply"
    elif route == "flag_for_human":
        actions = [
            Action(verb=a.get("verb", ""), subject=a.get("subject", ""), deadline=a.get("deadline"))
            for a in (r3d.data.get("actions") or [])
            if isinstance(a, dict)
        ]
        server.handle("flag_for_human", {"actions": [a.model_dump() for a in actions]})
        next_step = "flag_for_human"
    else:
        next_step = DEFAULT_NEXT_STEP  # no_action
    nodes.append(
        NodeDebug(
            node="decide",
            input=decide_prompt,
            output=r3d.content,
            parameters={
                "route": route,
                "draft": draft,
                "actions": [a.model_dump() for a in actions],
            },
            thinking=r3d.thinking,
        )
    )

    return InferenceResult(
        label=Label(
            actions=actions,
            next_step=next_step,
            draft=draft if next_step == "reply" else DEFAULT_DRAFT,
        ),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        invocations=server.invocations,
        debug=Debug(nodes=nodes),
    )


def run(
    emails: list[Email], model: str, ollama_url: str, temperature: float, think: bool
) -> list[RunResult]:
    results = []
    for email in emails:
        t0 = time.monotonic()
        inferred = run_email(email, model, ollama_url, temperature, think)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        results.append(
            RunResult(
                setup=2,
                email_id=email.id,
                predicted=inferred.label,
                metrics=Metrics(
                    tokens_in=inferred.tokens_in,
                    tokens_out=inferred.tokens_out,
                    wall_clock_ms=elapsed_ms,
                ),
                debug=inferred.debug,
            )
        )
    return results
