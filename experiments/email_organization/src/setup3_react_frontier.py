"""Setup 3: ReAct loop, frontier model via OpenRouter."""

from __future__ import annotations

import json
import time

from openai import OpenAI

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

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MAX_STEPS = 12

# Generic agent loop + the triage skill (the declarative peer of setup 2's graph).
_SYSTEM = f"{GENERIC_AGENT_SYSTEM}\n\n{EMAIL_TRIAGE_SKILL}"


def run_email(email: Email, model: str, api_key: str, temperature: float) -> InferenceResult:
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE)
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
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=temperature,
        )
        choice = resp.choices[0]
        msg = choice.message
        tokens_in += resp.usage.prompt_tokens if resp.usage else 0
        tokens_out += resp.usage.completion_tokens if resp.usage else 0

        loops.append(
            LoopDebug(
                input=[dict(m) for m in messages],
                output=msg.model_dump(),
                thinking=getattr(msg, "reasoning", None),
            )
        )
        messages.append(msg.model_dump())

        if not msg.tool_calls:
            break

        done = False
        for tc in msg.tool_calls:
            name = tc.function.name
            args: dict[str, object] = json.loads(tc.function.arguments or "{}")
            if name == "reply":
                state["next_step"] = "reply"
                state["draft"] = args.get("message")
                done = True
            elif name == "flag_for_human":
                state["next_step"] = "flag_for_human"
                state["actions"] = args.get("actions", [])
                done = True
            result = server.handle(name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
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


def run(emails: list[Email], model: str, api_key: str, temperature: float) -> list[RunResult]:
    results = []
    for email in emails:
        t0 = time.monotonic()
        inferred = run_email(email, model, api_key, temperature)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"[setup3] {email.id}: {inferred.steps} ReAct loop iteration(s)")
        results.append(
            RunResult(
                setup=3,
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
