from __future__ import annotations

import json
from pathlib import Path

from loop_workflow_comparison.models import Email


def load_emails(path: str | Path = "datasets/emails_smoke.json") -> list[Email]:
    with open(path) as f:
        raw = json.load(f)
    return [Email.model_validate(item) for item in raw]
