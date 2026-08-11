"""Generate the scaffolding for datasets/emails_individual.json.

This produces 50 email objects with empty headers/body, empty actions, null
draft, and blank scenario/difficulty/note. Only ``label.next_step``,
``category`` and ``notes_conflict`` are populated, following the distribution
documented in ../README.md and datasets/EMAIL_GUIDELINE.md.

``tool_returns`` is scaffolded with the canonical read-tool values
(``READ_TOOL_DEFAULTS`` below) so every email answers each read tool explicitly;
the person filling in the email overrides only the interesting deviations.

Writing over a dataset whose bodies are already filled in destroys hand-written
work, so that needs --force. Run from the experiment root:

    python datasets/scripts/generate_individual.py [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

# Script lives in datasets/scripts/; the dataset is written to datasets/.
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "emails_individual.json"

# Canonical "nothing special" return for each read tool: empty calendar = free,
# sender is a known contact, no standing notes. Every scaffolded email carries these
# explicitly (the mock server never defaults — see src/mcp_server.py), so a read
# tool always has an answer; hand-labeling overrides only the interesting deviations.
READ_TOOL_DEFAULTS: dict[str, dict[str, object]] = {
    "check_calendar_available": {"available": True},
    "check_unknown_sender": {"known": True},
    "get_note": {"notes": []},
}


class Segment(BaseModel):
    """A contiguous block of scaffolded emails sharing the same labels.

    ``category`` (multi-ask vs. buried, etc.) is written onto each email so the
    generation bucket survives independently of this script for whoever
    hand-writes the subjects and bodies later. It is dataset metadata for slicing
    results — the agent never emits an email type (see ../README.md "Task").
    """

    count: int
    category: str
    next_step: str
    # Marks the block whose get_note fixture is written to mislead. Kept off
    # ``category`` on purpose: it is a property of the fixture, not the email shape,
    # so the two stay independently sliceable.
    notes_conflict: bool = False


TOTAL = 50

# Distribution (50 emails), mirroring README.md. next_step is balanced near-evenly
# (17 reply / 17 flag_for_human / 16 no_action) so no route can be scored well by
# riding the class prior — at 40 emails flag_for_human was 47.5% of the set and an
# always-flag baseline scored 0.475 for free.
#
# no_action can only come from promotional and fyi, which is what sets those two
# category shares; the rest of the mass goes to single-ask.
SEGMENTS: list[Segment] = [
    Segment(count=10, category="promotional", next_step="no_action"),
    Segment(count=6, category="fyi", next_step="no_action"),
    Segment(count=4, category="fyi", next_step="reply"),
    Segment(count=6, category="single-ask", next_step="reply"),
    Segment(count=1, category="single-ask", next_step="reply", notes_conflict=True),
    Segment(count=5, category="single-ask", next_step="flag_for_human"),
    Segment(count=1, category="single-ask", next_step="flag_for_human", notes_conflict=True),
    Segment(count=3, category="multi-ask", next_step="reply"),
    Segment(count=2, category="multi-ask", next_step="flag_for_human"),
    Segment(count=1, category="multi-ask", next_step="flag_for_human", notes_conflict=True),
    Segment(count=2, category="buried", next_step="reply"),
    Segment(count=1, category="buried", next_step="reply", notes_conflict=True),
    Segment(count=3, category="buried", next_step="flag_for_human"),
    Segment(count=5, category="suspicious", next_step="flag_for_human"),
]


def build_emails() -> list[dict]:
    emails: list[dict] = []
    for segment in SEGMENTS:
        for _ in range(segment.count):
            emails.append(
                {
                    "id": f"e{len(emails) + 1:03d}",
                    "headers": {
                        "from": "",
                        "to": "",
                        "cc": "",
                        "date": "",
                        "subject": "",
                    },
                    "body": "",
                    "label": {
                        "actions": [],
                        "next_step": segment.next_step,
                        "draft": None,
                    },
                    "category": segment.category,
                    "difficulty": "",
                    "scenario": "",
                    "note": "",
                    "notes_conflict": segment.notes_conflict,
                    "tool_returns": {
                        tool: dict(value) for tool, value in READ_TOOL_DEFAULTS.items()
                    },
                }
            )
    return emails


def written_emails(path: Path) -> int:
    """How many emails at ``path`` already have a body, i.e. hand-written work that
    regenerating would destroy. 0 if the file is absent or unreadable as a dataset."""
    if not path.exists():
        return 0
    try:
        existing = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    return sum(1 for e in existing if e.get("body", "").strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite even if the existing dataset has hand-written bodies.",
    )
    args = parser.parse_args()

    emails = build_emails()
    total = sum(segment.count for segment in SEGMENTS)
    assert len(emails) == total == TOTAL, (len(emails), total)

    filled = written_emails(OUTPUT_PATH)
    if filled and not args.force:
        sys.exit(
            f"Refusing to overwrite {OUTPUT_PATH}: {filled} emails already have bodies.\n"
            "The scaffold is blank, so this would discard them. Re-run with --force if "
            "that is what you want."
        )

    OUTPUT_PATH.write_text(json.dumps(emails, indent=2) + "\n")

    category = Counter(e["category"] for e in emails)
    next_step = Counter(e["label"]["next_step"] for e in emails)
    print(f"Wrote {len(emails)} emails to {OUTPUT_PATH}")
    print("category:", dict(category))
    print("next_step:", dict(next_step))
    print("notes_conflict:", sum(1 for e in emails if e["notes_conflict"]))


if __name__ == "__main__":
    main()
