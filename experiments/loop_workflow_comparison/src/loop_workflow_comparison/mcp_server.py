from __future__ import annotations

import time
from typing import Any

from loop_workflow_comparison.models import Email, ToolInvocation
from loop_workflow_comparison.prompts import render_email

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_new_email",
            "description": "Fetch the next email from the inbox to process. Call this first.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_calendar_available",
            "description": (
                "Check whether the user's calendar is free for a proposed time. Use for "
                'scheduling asks. Returns {"available": <bool>}.'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "The proposed date/time."},
                    "length": {"type": "string", "description": "Duration, e.g. '30m', '1h'."},
                },
                "required": ["time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_unknown_sender",
            # Deliberately neutral: saying "use to spot suspicious senders" told the
            # model that not-a-contact means suspicious, which is false for
            # promotional mail. Let the model infer what unknown means from context.
            "description": (
                "Check whether an email sender is in the user's contacts. "
                'Returns {"known": <bool>}.'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sender": {"type": "string", "description": "The sender's email address."}
                },
                "required": ["sender"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_note",
            # Takes no arguments: retrieval is out of scope for this experiment, so the
            # store is assumed to always surface the notes relevant to this email. The
            # fixture may add unrelated notes as noise — reading past those is the
            # skill being exercised, not finding the right one.
            "description": (
                "Retrieve the user's standing notes and preferences — how they want kinds "
                "of email handled, policies, personal facts. Returns "
                '{"notes": [<text>, ...]}, which may include unrelated notes.'
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply",
            "description": (
                "Reply directly to the email. Only call when you have enough information to "
                "answer the user's request yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "no_action",
            # Explicit rather than "absence of a routing call". A single-shot node with
            # only reply/flag_for_human in front of it cannot express no-action by
            # silence — the model reliably picks a tool — and in the loop, silence was
            # indistinguishable from wandering off. ``reason`` is required so this is
            # not the cheapest route to emit: with no arguments it was the shortest
            # token path of the three, which biases selection toward it for reasons
            # that have nothing to do with the email.
            "description": (
                "Take no action on this email and leave it in the inbox. Use when the "
                "email needs nothing from you, including informational mail that does "
                "not require a response or follow-up. Use for promotional, fyi, "
                "automated, or CC-only emails when nothing needs to be done. Do not use "
                "when you are merely unsure; flag those instead. State why nothing is "
                "needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why this email needs nothing from you.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_for_human",
            "description": (
                "Flag the email for the human to handle, because the action is out of scope "
                "for the agent or needs information the agent doesn't have. Pass the action "
                "items that require attention (verb, subject, optional deadline); an empty "
                "list is allowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "verb": {"type": "string"},
                                "subject": {"type": "string"},
                                "deadline": {"type": ["string", "null"]},
                            },
                            "required": ["verb", "subject"],
                        },
                    }
                },
                "required": [],
            },
        },
    },
]

# The routing decision: these record the model's answer rather than query the
# environment, so they acknowledge with {"ok": True} while every other tool returns
# the email's fixture. Exactly one is expected per email; calling none is an error,
# not a silent no_action.
ROUTE_TOOLS = {"reply", "no_action", "flag_for_human"}

READ_TOOLS = {"check_calendar_available", "check_unknown_sender", "get_note"}


def call_role(tool_names: list[str]) -> str:
    """What a model call was for, from the tools it chose. Precedence matters: a turn
    that gathered and then routed is a decide turn, because that is what it settled."""
    if any(n in ROUTE_TOOLS for n in tool_names):
        return "decide"
    if any(n in READ_TOOLS for n in tool_names):
        return "gather"
    if "get_new_email" in tool_names:
        return "fetch"
    return "none"


def tools_for(names: set[str]) -> list[dict[str, Any]]:
    """The subset of TOOLS with the given names, for exposing a per-node tool set.

    The graph setup offers only the tools its current node may use, so both setups
    hand the model the same schemas — the difference between them is which tools are
    reachable when, not how the model is asked to call them.
    """
    return [t for t in TOOLS if t["function"]["name"] in names]


# The mock server is intentionally strict: it never defaults a read tool. A call
# with no explicit fixture raises UnknownToolError below instead of being silently
# answered (which could hide bugs), so every value the environment returns must be
# explicit in the data. The canonical "nothing special" shapes used to scaffold
# Email.tool_returns are a data-generation concern and live there, not here (see
# datasets/scripts/generate_individual.py).


class UnknownToolError(Exception):
    """Raised for a tool call the fixtures cannot answer — an unknown tool name or a
    read tool with no entry in Email.tool_returns.

    Setups catch this and fail the single email with ErrorKind "unknown_tool" rather
    than letting it abort the whole run.
    """


class MockMCPServer:
    def __init__(self, email: Email) -> None:
        self._email = email
        # Fixed return values keyed by tool name; see Email.tool_returns.
        self._tool_returns = email.tool_returns
        self._invocations: list[ToolInvocation] = []

    def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self._invocations.append(
            ToolInvocation(
                name=name,
                args=args,
                timestamp_ms=int(time.monotonic() * 1000),
            )
        )
        if name == "get_new_email":
            return {"email": render_email(self._email)}
        if name in ROUTE_TOOLS:
            return {"ok": True}
        if name in self._tool_returns:
            return self._tool_returns[name]

        raise UnknownToolError(f"Unexpected tool call: {name} with args {args}")

    def reset(self) -> None:
        self._invocations.clear()

    @property
    def invocations(self) -> list[ToolInvocation]:
        return list(self._invocations)
