from __future__ import annotations

import time
from typing import Any

from src.models import ToolInvocation

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "classify",
            "description": (
                "Classify the email into exactly one type: "
                "action_required, fyi, promotional, or suspicious."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "classification": {
                        "type": "string",
                        "enum": [
                            "action_required",
                            "fyi",
                            "promotional",
                            "suspicious",
                        ],
                    }
                },
                "required": ["classification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_actions",
            "description": (
                "Extract the action items in the email that require human "
                "attention. Each action has a verb, a subject, and an optional "
                "deadline (null if none)."
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
                "required": ["actions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decide_next_step",
            "description": (
                "Decide the next step for this email. "
                "reply: the agent has enough information and can reply directly to the user. "
                "no_action: no action from the agent - either the email does not require one "
                "(CC'd emails, promotional emails, automated notifications) or it is left in the "
                "inbox for the user to see; the default when unsure. "
                "flag_for_human: needs doing, but the agent can't - either out of scope (action "
                "in another system, e.g. paying) or missing information (e.g. availability)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "next_step": {
                        "type": "string",
                        "enum": ["reply", "no_action", "flag_for_human"],
                    }
                },
                "required": ["next_step"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_reply",
            "description": "Draft a reply to the email. Only call if next_step is reply.",
            "parameters": {
                "type": "object",
                "properties": {"draft": {"type": "string"}},
                "required": ["draft"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that processing is complete.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


class MockMCPServer:
    def __init__(self) -> None:
        self._invocations: list[ToolInvocation] = []

    def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self._invocations.append(
            ToolInvocation(
                name=name,
                args=args,
                timestamp_ms=int(time.monotonic() * 1000),
            )
        )
        return {"ok": True}

    def reset(self) -> None:
        self._invocations.clear()

    @property
    def invocations(self) -> list[ToolInvocation]:
        return list(self._invocations)

    def verify_sequence(self, expected: list[str]) -> bool:
        actual = [inv.name for inv in self._invocations]
        return actual == expected
