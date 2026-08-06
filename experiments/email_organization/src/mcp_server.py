from __future__ import annotations

import time
from typing import Any

from src.models import Email, ToolInvocation
from src.prompts import render_email

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
            "description": (
                "Check whether an email sender is a known contact. Use to spot suspicious "
                'senders. Returns {"known": <bool>}.'
            ),
            "parameters": {
                "type": "object",
                "properties": {"sender": {"type": "string"}},
                "required": ["sender"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_note",
            "description": (
                "Look up a fact in the user's personal notes (preferences, PTO, policies, "
                'etc.) to answer a question directly. Returns {"note": <text, or null if '
                "there is no such note>}."
            ),
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
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

# Tools that record the model's answer rather than query the environment. They
# acknowledge with {"ok": True}; every other tool returns the email's fixture.
REPORT_TOOLS = {"reply", "flag_for_human"}

# The mock server is intentionally strict: it never defaults a read tool. A call
# with no explicit fixture fails loudly in handle() below instead of being silently
# answered (which could hide bugs), so every value the environment returns must be
# explicit in the data. The canonical "nothing special" shapes used to scaffold
# Email.tool_returns are a data-generation concern and live there, not here (see
# datasets/scripts/generate_individual.py).

# Fallback for a tool with neither a fixture nor a documented default.
NO_DATA: dict[str, Any] = {"found": False}


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
        if name in REPORT_TOOLS:
            return {"ok": True}
        if name in self._tool_returns:
            return self._tool_returns[name]

        # TODO: this will fail the whole run if not handled up stream, okay for now
        # if run takes too long, handle upstream to skip the data point
        raise ValueError(f"Unexpected tool call: {name} with args {args}")

    def reset(self) -> None:
        self._invocations.clear()

    @property
    def invocations(self) -> list[ToolInvocation]:
        return list(self._invocations)

    def verify_sequence(self, expected: list[str]) -> bool:
        actual = [inv.name for inv in self._invocations]
        return actual == expected
