"""Shared prompt helpers for the email-organization setups.

``CURRENT_TIME`` is pinned to a fixed value so "now" is not a source of
variability across runs (the model always reasons about deadlines relative to
the same wall clock). It is deliberately arbitrary — here, last Wednesday.
"""

from __future__ import annotations

from src.models import Email

# Fixed "now" injected into every prompt so time is held constant across runs.
CURRENT_TIME = "Wed, 29 Jul 2026 09:40:10 +0400"


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
