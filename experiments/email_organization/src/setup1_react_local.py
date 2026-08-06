"""Setup 1: ReAct loop, local 9B via Ollama."""

from __future__ import annotations

import json
import time

import ollama

from src.mcp_server import TOOLS, MockMCPServer
from src.models import (
    DEFAULT_DRAFT,
    DEFAULT_NEXT_STEP,
    Action,
    Debug,
    Email,
    InferenceResult,
    Label,
    LoopDebug,
    Metrics,
    RunResult,
)
from src.prompts import EMAIL_TRIAGE_SKILL, GENERIC_AGENT_SYSTEM

MAX_STEPS = 12

# Generic agent loop + the triage skill (the declarative peer of setup 2's graph).
_SYSTEM = f"{GENERIC_AGENT_SYSTEM}\n\n{EMAIL_TRIAGE_SKILL}"


def run_email(
    email: Email, model: str, ollama_url: str, temperature: float, think: bool
) -> InferenceResult:
    client = ollama.Client(host=ollama_url)
    server = MockMCPServer(email)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "Process the next email in the inbox."},
    ]

    state: dict[str, object] = {"actions": [], "next_step": None, "draft": None}
    tokens_in = tokens_out = 0
    steps = 0
    loops: list[LoopDebug] = []

    for step in range(MAX_STEPS):
        steps = step + 1
        resp = client.chat(
            model=model,
            messages=messages,
            tools=TOOLS,
            think=think,
            options={"temperature": temperature},
        )
        msg = resp.message
        tokens_in += resp.prompt_eval_count or 0
        tokens_out += resp.eval_count or 0
        loops.append(
            LoopDebug(
                input=[dict(m) for m in messages],
                output=msg.model_dump(),
                thinking=getattr(msg, "thinking", None) if think else None,
            )
        )
        messages.append(msg.model_dump())

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
            elif name == "flag_for_human":
                state["next_step"] = "flag_for_human"
                state["actions"] = args.get("actions", [])
                done = True
            result = server.handle(name, args)
            messages.append({"role": "tool", "content": json.dumps(result), "name": name})
        # reply / flag_for_human are terminal routing decisions.
        if done:
            break

    label = Label(
        actions=[Action(**a) for a in state["actions"]],  # type: ignore[arg-type]
        next_step=str(state["next_step"] or DEFAULT_NEXT_STEP),
        draft=str(state["draft"]) if state["draft"] else DEFAULT_DRAFT,
    )
    return InferenceResult(
        label=label,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        steps=steps,
        invocations=server.invocations,
        debug=Debug(loops=loops),
    )


def run(
    emails: list[Email], model: str, ollama_url: str, temperature: float, think: bool
) -> list[RunResult]:
    results = []
    for email in emails:
        t0 = time.monotonic()
        inferred = run_email(email, model, ollama_url, temperature, think)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"[setup1] {email.id}: {inferred.steps} ReAct loop iteration(s)")
        results.append(
            RunResult(
                setup=1,
                email_id=email.id,
                predicted=inferred.label,
                metrics=Metrics(
                    tokens_in=inferred.tokens_in,
                    tokens_out=inferred.tokens_out,
                    wall_clock_ms=elapsed_ms,
                    steps=inferred.steps,
                ),
                debug=inferred.debug,
            )
        )
    return results
