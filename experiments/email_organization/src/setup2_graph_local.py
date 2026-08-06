"""Setup 2: Fixed graph workflow, local 9B via Ollama."""

from __future__ import annotations

import json
import time

import ollama
from pydantic import BaseModel

from src.metrics import label_correct
from src.models import (
    DEFAULT_CLASSIFICATION,
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
from src.prompts import render_email

_EARLY_EXIT_CLASSES = {"promotional", "fyi"}


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
    tokens_in = tokens_out = 0
    nodes: list[NodeDebug] = []

    # Node 1: classify
    classify_prompt = (
        f"Classify this email into exactly one type: action_required, fyi, promotional, or suspicious.\n"
        f'Respond with JSON: {{"classification": "<value>"}}\n\n'
        f"{render_email(email)}"
    )
    r1 = _chat_json(client, model, temperature, think, classify_prompt)
    tokens_in += r1.tokens_in
    tokens_out += r1.tokens_out
    classification = r1.data.get("classification", DEFAULT_CLASSIFICATION)
    if classification not in {"action_required", "fyi", "promotional", "suspicious"}:
        classification = DEFAULT_CLASSIFICATION
    nodes.append(
        NodeDebug(
            node="classify",
            input=classify_prompt,
            output=r1.content,
            parameters={"classification": classification},
            thinking=r1.thinking,
        )
    )

    # Early exit for promotional / fyi
    if classification in _EARLY_EXIT_CLASSES:
        return InferenceResult(
            label=Label(classification=classification, actions=[], next_step="no_action", draft=None),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            debug=Debug(nodes=nodes),
        )

    # Node 2: extract actions
    actions_prompt = (
        f"Extract the action items in this email that require human attention. Each action has: verb (str), subject (str), deadline (str or null).\n"
        f'Respond with JSON: {{"actions": [{{"verb": ..., "subject": ..., "deadline": ...}}]}}\n\n'
        f"{render_email(email)}"
    )
    r2 = _chat_json(client, model, temperature, think, actions_prompt)
    tokens_in += r2.tokens_in
    tokens_out += r2.tokens_out
    raw_actions = r2.data.get("actions", [])
    actions = [
        Action(verb=a.get("verb", ""), subject=a.get("subject", ""), deadline=a.get("deadline"))
        for a in raw_actions
    ]
    nodes.append(
        NodeDebug(
            node="extract_actions",
            input=actions_prompt,
            output=r2.content,
            parameters={"actions": [a.model_dump() for a in actions]},
            thinking=r2.thinking,
        )
    )

    # Node 3: decide next step
    next_step_prompt = (
        f"Given this email, decide the next step: reply, no_action, or flag_for_human.\n"
        f"- reply: you have enough information to answer the user directly.\n"
        f"- no_action: no action needed, or leave it in the inbox for the user to see; the default when unsure.\n"
        f"- flag_for_human: needs doing but the agent can't - out of scope or missing information.\n"
        f'Respond with JSON: {{"next_step": "<value>"}}\n\n'
        f"{render_email(email)}"
    )
    r3 = _chat_json(client, model, temperature, think, next_step_prompt)
    tokens_in += r3.tokens_in
    tokens_out += r3.tokens_out
    next_step = r3.data.get("next_step", DEFAULT_NEXT_STEP)
    if next_step not in {"reply", "no_action", "flag_for_human"}:
        next_step = DEFAULT_NEXT_STEP
    nodes.append(
        NodeDebug(
            node="decide_next_step",
            input=next_step_prompt,
            output=r3.content,
            parameters={"next_step": next_step},
            thinking=r3.thinking,
        )
    )

    draft = None
    if next_step == "reply":
        # Node 4: draft reply
        draft_prompt = (
            f"Draft a concise professional reply to this email.\n\n" f"{render_email(email)}"
        )
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": draft_prompt}],
            think=think,
            options={"temperature": temperature},
        )
        draft = resp.message.content.strip()
        tokens_in += resp.prompt_eval_count or 0
        tokens_out += resp.eval_count or 0
        nodes.append(
            NodeDebug(
                node="draft_reply",
                input=draft_prompt,
                output=resp.message.content or "",
                parameters={"draft": draft},
                thinking=getattr(resp.message, "thinking", None) if think else None,
            )
        )

    return InferenceResult(
        label=Label(
            classification=classification, actions=actions, next_step=next_step, draft=draft
        ),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
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
                    correct=label_correct(inferred.label, email.label),
                    tokens_in=inferred.tokens_in,
                    tokens_out=inferred.tokens_out,
                    wall_clock_ms=elapsed_ms,
                ),
                debug=inferred.debug,
            )
        )
    return results
