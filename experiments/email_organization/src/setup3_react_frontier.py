"""Setup 3: ReAct loop, frontier model via OpenRouter."""

from __future__ import annotations

import json
import time

from openai import OpenAI

from src.mcp_server import TOOLS, MockMCPServer
from src.metrics import label_correct
from src.models import (
    DEFAULT_CLASSIFICATION,
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
from src.prompts import render_email

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MAX_STEPS = 12

_SYSTEM = (
    "You are an email triage assistant. Process the email using the provided tools in order: "
    "classify → extract_actions → decide_next_step → draft_reply (only if reply) → finish.\n"
    "- classify: pick exactly one type — action_required, fyi, promotional, or suspicious.\n"
    "- extract_actions: list the action items in the email that require human attention "
    "(verb, subject, optional deadline).\n"
    "- decide_next_step: reply (you have enough information to answer the user directly), "
    "no_action (no action needed, or leave it in the inbox for the user to see; the default "
    "when unsure), or flag_for_human (needs doing but the agent can't - out of scope or "
    "missing information).\n"
    "- draft_reply: only when next_step is reply.\n"
    "Call finish when done."
)


def run_email(email: Email, model: str, api_key: str, temperature: float) -> InferenceResult:
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE)
    server = MockMCPServer()
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": render_email(email)},
    ]

    state: dict[str, object] = {
        "classification": None,
        "actions": [],
        "next_step": None,
        "draft": None,
    }
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
            if name == "classify":
                state["classification"] = args.get("classification")
            elif name == "extract_actions":
                state["actions"] = args.get("actions", [])
            elif name == "decide_next_step":
                state["next_step"] = args.get("next_step")
            elif name == "draft_reply":
                state["draft"] = args.get("draft")
            elif name == "finish":
                done = True
            result = server.handle(name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
        if done:
            break

    label = Label(
        classification=str(state["classification"] or DEFAULT_CLASSIFICATION),
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
                    correct=label_correct(inferred.label, email.label),
                    tokens_in=inferred.tokens_in,
                    tokens_out=inferred.tokens_out,
                    wall_clock_ms=elapsed_ms,
                    steps=inferred.steps,
                ),
                debug=inferred.debug,
            )
        )
    return results
