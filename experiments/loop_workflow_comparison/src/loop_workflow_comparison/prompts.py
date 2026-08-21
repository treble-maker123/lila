"""Shared prompt helpers for the email-organization setups.

``CURRENT_TIME`` is pinned to a fixed value so "now" is not a source of
variability across runs (the model always reasons about deadlines relative to
the same wall clock). The date is arbitrary — here, last Wednesday — but the
offset is not: the dataset is written in US Eastern and every clock time in a
body is ET, so no email asks the model to convert between zones.
"""

from __future__ import annotations

import json
from typing import Any

from loop_workflow_comparison.models import Email

# Fixed "now" injected into every prompt so time is held constant across runs.
CURRENT_TIME = "Wed, 29 Jul 2026 09:40:10 -0400"

# Task-agnostic agent-loop prompt. Deliberately generic so it is NOT tuned for
# email triage: the ReAct setups pair it with EMAIL_TRIAGE_SKILL below, which is
# the declarative peer of the fixed control flow the graph setup encodes in code.
# Keeping the two separate lets us toggle the skill (generic agent with vs.
# without the procedure) and keeps the loop-vs-graph comparison apples-to-apples.
GENERIC_AGENT_SYSTEM = (
    "You are an autonomous assistant that completes tasks using the tools provided. "
    "Work in a loop: call tools to gather information and to take actions, read each "
    "result, and continue until the task is done. Use only the available tools, and "
    "stop once you have done what the task requires."
)

# The reply / flag_for_human / no-action definitions, worded once and shared by both
# the ReAct skill (EMAIL_TRIAGE_SKILL) and the graph's decide node (setup 2), so
# neither setup gets a subtly better description of the task. Each setup wraps these
# definitions in its own mechanism framing (a tool call vs. a JSON route value).
ROUTING_POLICY = (
    "- reply: you have enough information to answer the sender directly.\n"
    "- flag_for_human: it needs doing but you can't - out of scope (action in another "
    "system, e.g. paying) or missing information. Provide the action items that need "
    "attention (verb, subject, optional deadline). This is also the default when you "
    "are unsure - surfacing an email is safer than burying it.\n"
    "- no_action: the email is promotional, fyi, automated, or you're only CC'd - "
    "nothing to do. Use only when you are confident nothing is needed, and say why.\n"
)

# When to call the read tools, worded once and shared like ROUTING_POLICY. States a
# test rather than a lean: the graph used to invite tool calls outright while the loop
# only mentioned gathering in passing, which put an uncontrolled prompt difference on
# top of the mechanism the experiment measures. Deliberately not firmer than this —
# "always check your notes first" would hand the loop the graph's behaviour by prompt
# and turn the comparison into instruction-following.
GATHER_POLICY = (
    "Look up context only when it would change how you handle this email. Call nothing "
    "if it would not.\n"
)

# How a setup tells the model to commit to a route. Shared so the loop and the graph
# name the same three tools with the same arity — no_action takes a reason precisely
# so it is not the cheapest tool to emit (see src/mcp_server.py).
ROUTING_INSTRUCTION = (
    "Act on the route by calling exactly one of reply(message), flag_for_human(actions) "
    "or no_action(reason).\n"
)

# The email-triage procedure, expressed as a skill the agent follows. This is the
# same procedure setup 2 encodes structurally in its graph, so do not duplicate it
# into a bespoke system prompt — pair it with GENERIC_AGENT_SYSTEM instead. The
# routing definitions are shared with the graph via ROUTING_POLICY and GATHER_POLICY.
EMAIL_TRIAGE_SKILL = (
    "# Skill: email triage\n"
    "Triage the user's inbox one email at a time.\n"
    "1. Call get_new_email to fetch the email to process.\n"
    # Deliberately does not enumerate the read tools: their schemas are already
    # passed to the model, and the graph's gather node doesn't enumerate them either.
    f"2. {GATHER_POLICY}"
    "3. Then route the email using these definitions:\n"
    f"{ROUTING_POLICY}"
    f"{ROUTING_INSTRUCTION}"
)


def render_tool_result(tool: str, result: dict[str, Any]) -> str:
    """Render one tool result as prompt text, identically for every setup.

    ``get_new_email`` is unwrapped to the rendered email rather than JSON-encoded:
    encoding it hands the loop a ``\\n``-escaped blob where the graph reads prose,
    which is a formatting difference masquerading as a control-flow one. Every other
    result is a small dict and goes through JSON in both setups.
    """
    if tool == "get_new_email":
        return str(result["email"])
    return json.dumps(result)


def render_email(email: Email) -> str:
    """Render an email (envelope + body) for inclusion in a prompt, prefixed
    with the fixed current time."""
    h = email.headers
    return (
        f"Current time: {CURRENT_TIME}\n\n"
        f"From: {h.from_}\n"
        f"To: {h.to}\n"
        f"Cc: {h.cc}\n"
        f"Date: {h.date}\n"
        f"Subject: {h.subject}\n\n"
        f"{email.body}"
    )
