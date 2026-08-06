"""Generate the scaffolding for datasets/emails_individual.json.

This produces 40 email objects with empty headers/body, empty actions, null
draft, and blank difficulty/why_challenging. Only ``label.classification`` and
``label.next_step`` are populated, following the distribution documented in
../README.md and datasets/EMAIL_GUIDELINE.md.

Run from the experiment root:

    python datasets/scripts/generate_individual.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

# Script lives in datasets/scripts/; the dataset is written to datasets/.
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "emails_individual.json"


class Segment(BaseModel):
    """A contiguous block of scaffolded emails sharing the same labels.

    ``category`` (multi-ask vs. buried, etc.) is written onto each email so the
    generation bucket survives independently of this script for whoever
    hand-writes the subjects and bodies later.
    """

    count: int
    category: str
    classification: str
    next_step: str


# Distribution (40 emails), mirroring README.md:
#   30% promotional / fyi (early exit -> no_action)
#   30% single-ask -> split evenly reply / flag_for_human
#   15% multi-ask   -> split evenly reply / flag_for_human
#   15% buried      -> split evenly reply / flag_for_human
#   10% suspicious  (-> flag_for_human)
#
# category is data labeling, classification is model output, a bit messy I know
SEGMENTS: list[Segment] = [
    Segment(count=6, category="promotional", classification="promotional", next_step="no_action"),
    Segment(count=3, category="fyi", classification="fyi", next_step="no_action"),
    Segment(count=3, category="fyi", classification="fyi", next_step="reply"),
    Segment(count=6, category="single-ask", classification="action_required", next_step="reply"),
    Segment(
        count=6, category="single-ask", classification="action_required", next_step="flag_for_human"
    ),
    Segment(count=3, category="multi-ask", classification="action_required", next_step="reply"),
    Segment(
        count=3, category="multi-ask", classification="action_required", next_step="flag_for_human"
    ),
    Segment(count=3, category="buried", classification="action_required", next_step="reply"),
    Segment(
        count=3, category="buried", classification="action_required", next_step="flag_for_human"
    ),
    Segment(
        count=4, category="suspicious", classification="suspicious", next_step="flag_for_human"
    ),
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
                        "classification": segment.classification,
                        "actions": [],
                        "next_step": segment.next_step,
                        "draft": None,
                    },
                    "category": segment.category,
                    "difficulty": "",
                    "why_challenging": "",
                }
            )
    return emails


def main() -> None:
    emails = build_emails()
    total = sum(segment.count for segment in SEGMENTS)
    assert len(emails) == total == 40, (len(emails), total)

    OUTPUT_PATH.write_text(json.dumps(emails, indent=2) + "\n")

    classification = Counter(e["label"]["classification"] for e in emails)
    next_step = Counter(e["label"]["next_step"] for e in emails)
    print(f"Wrote {len(emails)} emails to {OUTPUT_PATH}")
    print("classification:", dict(classification))
    print("next_step:", dict(next_step))


if __name__ == "__main__":
    main()
