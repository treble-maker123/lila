"""Scaffold datasets/emails_individual.json, and check hand-written content back.

The scaffold leaves headers, body, actions, scenario, difficulty and note blank.
It fixes every property that has to be balanced across the set — labels from
SEGMENTS, fixture values and authoring targets from SLOTS — because a property
left to whoever writes the prose ends up correlated with the answer. The first
hand-written round proved it: the calendar was ``true`` on 49 of 50 emails, no
unknown sender appeared on a ``reply``, and every body landed in 120-153 words.

    python datasets/scripts/generate_individual.py [--force]   # write the scaffold
    python datasets/scripts/generate_individual.py --check     # validate what was written

Run from the experiment root. --force is needed to discard existing bodies.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
from pathlib import Path

from pydantic import BaseModel

# Script lives in datasets/scripts/; the dataset is written to datasets/.
DATASETS_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = DATASETS_DIR / "emails_individual.json"
PLAN_PATH = DATASETS_DIR / "emails_individual_plan.md"

# The experiment root, so ``src`` imports work when this is run as a script path.
sys.path.insert(0, str(DATASETS_DIR.parent))

from src.prompts import CURRENT_TIME  # noqa: E402

INBOX_OWNER = "Shannon C. <shannon@info4days.edu>"

# Body length bands, in words. Naming the bands is what makes the guideline's
# 120-750 range checkable instead of aspirational.
BANDS: dict[str, tuple[int, int]] = {
    "S": (120, 180),
    "M": (200, 350),
    "L": (400, 550),
    "XL": (600, 750),
}

# A ``get_note`` entry is either a one-liner or a pasted artifact (minutes, a legal
# review, a week of standups). Uniformly terse notes make the relevant one too easy
# to spot.
SHORT_NOTE_MAX_WORDS = 45
LONG_NOTE_MIN_WORDS = 100
LONG_NOTE_MAX_WORDS = 150

# What the calendar fixture is for. ``irrelevant`` emails still answer the tool (the
# mock server has no defaults) but are written so the value changes nothing, and are
# split across both values — constant-``true`` would make the tool free evidence for
# ``reply``.
CalendarRole = str  # "settles" | "blocks" | "irrelevant"


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


class Slot(BaseModel):
    """The per-email axes that have to be balanced across the set.

    Every field constrains the email written into the slot rather than describing
    one. ``known``/``available`` land in the dataset directly; the rest are
    authoring targets ``--check`` verifies after the fact.
    """

    known: bool
    available: bool
    calendar: CalendarRole
    # How many get_note entries this email's fixture should carry.
    notes: int
    # Whether one of them is a pasted artifact rather than a one-liner.
    long_note: bool = False
    band: str


TOTAL = 50

# Distribution (50 emails), mirroring README.md. Promotional includes two
# flag_for_human rows so the category is not a free no_action shortcut.
#
# no_action can only come from promotional and fyi, which is what sets those two
# category shares; the rest of the mass goes to single-ask.
SEGMENTS: list[Segment] = [
    Segment(count=4, category="promotional", next_step="no_action"),
    Segment(count=1, category="promotional", next_step="no_action", notes_conflict=True),
    Segment(count=3, category="promotional", next_step="no_action"),
    Segment(count=2, category="promotional", next_step="flag_for_human"),
    Segment(count=1, category="fyi", next_step="no_action"),
    Segment(count=1, category="fyi", next_step="no_action", notes_conflict=True),
    Segment(count=4, category="fyi", next_step="no_action"),
    Segment(count=4, category="fyi", next_step="reply"),
    Segment(count=1, category="single-ask", next_step="reply", notes_conflict=True),
    Segment(count=5, category="single-ask", next_step="reply"),
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

# Per-email axes, written out rather than derived so the balance is readable off
# the table. The marginals these rows add up to are asserted in ``check_balance``.
SLOTS: dict[str, Slot] = {
    # --- promotional / no_action -------------------------------------------------
    "e001": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="M"),
    "e002": Slot(known=True, available=False, calendar="irrelevant", notes=1, band="S"),
    "e003": Slot(known=True, available=True, calendar="irrelevant", notes=0, band="L"),
    "e004": Slot(known=False, available=False, calendar="irrelevant", notes=0, band="M"),
    "e005": Slot(known=True, available=True, calendar="irrelevant", notes=2, band="L"),
    "e006": Slot(known=True, available=False, calendar="irrelevant", notes=2, band="XL"),
    "e007": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="S"),
    "e008": Slot(known=True, available=False, calendar="irrelevant", notes=1, band="M"),
    # --- promotional / flag_for_human --------------------------------------------
    "e009": Slot(known=False, available=True, calendar="irrelevant", notes=1, band="L"),
    "e010": Slot(known=True, available=False, calendar="irrelevant", notes=1, band="M"),
    # --- fyi / no_action ----------------------------------------------------------
    "e011": Slot(known=True, available=True, calendar="irrelevant", notes=1, band="S"),
    "e012": Slot(known=True, available=False, calendar="irrelevant", notes=2, band="M"),
    "e013": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="M"),
    "e014": Slot(known=True, available=False, calendar="irrelevant", notes=0, band="M"),
    "e015": Slot(
        known=True, available=True, calendar="irrelevant", notes=3, long_note=True, band="XL"
    ),
    "e016": Slot(known=True, available=False, calendar="irrelevant", notes=0, band="L"),
    # --- fyi / reply --------------------------------------------------------------
    "e017": Slot(known=True, available=True, calendar="irrelevant", notes=1, band="S"),
    "e018": Slot(known=True, available=True, calendar="settles", notes=0, band="M"),
    "e019": Slot(
        known=True, available=False, calendar="irrelevant", notes=3, long_note=True, band="L"
    ),
    "e020": Slot(known=True, available=True, calendar="irrelevant", notes=0, band="M"),
    # --- single-ask / reply -------------------------------------------------------
    "e021": Slot(known=True, available=True, calendar="settles", notes=2, band="M"),
    "e022": Slot(known=False, available=False, calendar="irrelevant", notes=0, band="S"),
    "e023": Slot(known=True, available=False, calendar="irrelevant", notes=0, band="M"),
    "e024": Slot(known=True, available=True, calendar="irrelevant", notes=0, band="L"),
    "e025": Slot(known=True, available=True, calendar="settles", notes=0, band="S"),
    "e026": Slot(known=False, available=False, calendar="irrelevant", notes=1, band="S"),
    "e027": Slot(known=True, available=True, calendar="irrelevant", notes=2, band="M"),
    # --- single-ask / flag_for_human ----------------------------------------------
    "e028": Slot(
        known=True, available=False, calendar="irrelevant", notes=2, long_note=True, band="M"
    ),
    "e029": Slot(known=True, available=False, calendar="blocks", notes=0, band="S"),
    "e030": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="S"),
    "e031": Slot(known=True, available=False, calendar="blocks", notes=1, band="M"),
    "e032": Slot(known=True, available=True, calendar="irrelevant", notes=0, band="M"),
    "e033": Slot(known=True, available=False, calendar="irrelevant", notes=2, band="M"),
    # --- multi-ask / reply ---------------------------------------------------------
    "e034": Slot(
        known=True, available=True, calendar="irrelevant", notes=2, long_note=True, band="XL"
    ),
    "e035": Slot(known=True, available=True, calendar="settles", notes=0, band="M"),
    "e036": Slot(known=True, available=False, calendar="irrelevant", notes=1, band="M"),
    # --- multi-ask / flag_for_human -------------------------------------------------
    "e037": Slot(
        known=True, available=True, calendar="irrelevant", notes=3, long_note=True, band="L"
    ),
    "e038": Slot(known=True, available=False, calendar="irrelevant", notes=0, band="XL"),
    "e039": Slot(known=True, available=True, calendar="irrelevant", notes=2, band="L"),
    # --- buried / reply --------------------------------------------------------------
    "e040": Slot(known=True, available=False, calendar="irrelevant", notes=0, band="M"),
    "e041": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="M"),
    "e042": Slot(known=True, available=False, calendar="irrelevant", notes=2, band="XL"),
    # --- buried / flag_for_human ------------------------------------------------------
    "e043": Slot(known=True, available=False, calendar="blocks", notes=0, band="L"),
    "e044": Slot(known=True, available=False, calendar="irrelevant", notes=1, band="XL"),
    "e045": Slot(known=True, available=True, calendar="irrelevant", notes=2, band="L"),
    # --- suspicious / flag_for_human ---------------------------------------------------
    "e046": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="S"),
    "e047": Slot(known=False, available=False, calendar="irrelevant", notes=0, band="S"),
    "e048": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="S"),
    "e049": Slot(known=False, available=False, calendar="irrelevant", notes=0, band="M"),
    "e050": Slot(known=False, available=True, calendar="irrelevant", notes=0, band="L"),
}


def scaffold() -> list[dict]:
    emails: list[dict] = []
    for segment in SEGMENTS:
        for _ in range(segment.count):
            email_id = f"e{len(emails) + 1:03d}"
            slot = SLOTS[email_id]
            emails.append(
                {
                    "id": email_id,
                    "headers": {"from": "", "to": "", "cc": "", "date": "", "subject": ""},
                    "body": "",
                    "label": {"actions": [], "next_step": segment.next_step, "draft": None},
                    "category": segment.category,
                    "difficulty": "",
                    "scenario": "",
                    "note": "",
                    "notes_conflict": segment.notes_conflict,
                    "tool_returns": {
                        "check_calendar_available": {"available": slot.available},
                        "check_unknown_sender": {"known": slot.known},
                        "get_note": {"notes": []},
                    },
                }
            )
    return emails


def check_balance(emails: list[dict]) -> list[str]:
    """The marginals SLOTS is supposed to produce. Wrong last round, so asserted."""
    problems: list[str] = []
    route = {e["id"]: e["label"]["next_step"] for e in emails}
    category = {e["id"]: e["category"] for e in emails}

    # An unknown sender must not mean "not a reply", and must not mean "suspicious".
    unknown_by_route = Counter(route[i] for i, s in SLOTS.items() if not s.known)
    for name in ("reply", "no_action", "flag_for_human"):
        if unknown_by_route[name] < 3:
            problems.append(
                f"check_unknown_sender: only {unknown_by_route[name]} known=false on {name}; "
                "needs >= 3 so an unknown sender never settles a route"
            )
    unknown_promotional = sum(
        1 for i, s in SLOTS.items() if not s.known and category[i] == "promotional"
    )
    if not 3 <= unknown_promotional <= 6:
        problems.append(
            f"check_unknown_sender: {unknown_promotional}/10 promotional are known=false; "
            "keep it mid-range so the category is not readable off the fixture"
        )

    # The calendar has to be falsifiable in both directions and actually decide
    # something in each.
    roles = Counter(s.calendar for s in SLOTS.values())
    if roles["settles"] < 3 or roles["blocks"] < 3:
        problems.append(f"check_calendar_available: load-bearing roles too thin: {dict(roles)}")
    for role, want in (("settles", True), ("blocks", False)):
        bad = [i for i, s in SLOTS.items() if s.calendar == role and s.available is not want]
        if bad:
            problems.append(f"check_calendar_available: {role} slots must be {want}: {bad}")
    available = Counter(s.available for s in SLOTS.values())
    if min(available.values()) < TOTAL * 0.3:
        problems.append(f"check_calendar_available: lopsided across the set: {dict(available)}")

    # Having notes at all must not predict the route.
    with_notes = Counter(route[i] for i, s in SLOTS.items() if s.notes)
    total_by_route = Counter(route.values())
    for name, n in total_by_route.items():
        share = with_notes[name] / n
        if not 0.3 <= share <= 0.65:
            problems.append(
                f"get_note: {with_notes[name]}/{n} {name} emails carry notes ({share:.0%}); "
                "keep every route between 30% and 65% so presence is not a tell"
            )
    long_notes = [i for i, s in SLOTS.items() if s.long_note]
    if len(long_notes) < 4:
        problems.append(f"get_note: only {len(long_notes)} long artifacts; needs >= 4")
    if len({route[i] for i in long_notes}) < 3:
        problems.append("get_note: long artifacts must span all three routes")
    for email_id in long_notes:
        if SLOTS[email_id].notes < 2:
            problems.append(
                f"get_note: {email_id} has a long artifact but no other notes to hide it in"
            )

    # Body length must span the guideline's range and not track the category.
    bands = Counter(s.band for s in SLOTS.values())
    if set(bands) != set(BANDS):
        problems.append(f"body length: unused bands: {sorted(set(BANDS) - set(bands))}")
    if bands["XL"] < 5 or bands["L"] < 10:
        problems.append(f"body length: too few long bodies: {dict(bands)}")
    by_category: dict[str, set[str]] = defaultdict(set)
    for email_id, slot in SLOTS.items():
        by_category[category[email_id]].add(slot.band)
    for name, used in by_category.items():
        if len(used) < 3:
            problems.append(f"body length: {name} spans only {sorted(used)}; needs >= 3 bands")
    if by_category["promotional"] != set(BANDS):
        problems.append("body length: promotional must span the full range")
    by_route: dict[str, set[str]] = defaultdict(set)
    for email_id, slot in SLOTS.items():
        by_route[route[email_id]].add(slot.band)
    for name, used in by_route.items():
        if len(used) < 3:
            problems.append(
                f"body length: route {name} spans only {sorted(used)}; needs >= 3 bands"
            )
    # The other direction, and the one that matters for the hypothesis: length is the
    # independent variable, so a band missing a route means "degrades on long emails"
    # cannot be told apart from "gets that route wrong". XL had no reply at all.
    band_routes: dict[str, set[str]] = defaultdict(set)
    for email_id, slot in SLOTS.items():
        band_routes[slot.band].add(route[email_id])
    for name, seen in band_routes.items():
        if len(seen) < 3:
            problems.append(f"body length: band {name} carries only {sorted(seen)}; needs all 3")

    # Misleading notes are what price unconditional gathering, so there have to be
    # enough of them to clear the noise floor, and they must not track a route.
    conflicts = [e["id"] for e in emails if e["notes_conflict"]]
    if len(conflicts) < 7:
        problems.append(f"notes_conflict: {len(conflicts)} items; needs >= 7 to be measurable")
    if len({route[i] for i in conflicts}) < 3:
        problems.append("notes_conflict: must span all three routes")
    thin = [i for i in conflicts if SLOTS[i].notes < 2]
    if thin:
        problems.append(f"notes_conflict: needs >= 2 notes to contradict each other: {thin}")

    return problems


DEADLINE_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"\d{4}( \d{2}:\d{2}| EOD)?$"
)
# Greetings and sign-offs recur in real mail; prose does not. Anything longer that
# appears twice is boilerplate, which is what leaked the label last round.
SHARED_LINE_MAX_WORDS = 8


def check_content(emails: list[dict]) -> list[str]:
    """Validate hand-written content against the scaffold and the guideline."""
    problems: list[str] = []
    now = parsedate_to_datetime(CURRENT_TIME)
    lines: dict[str, list[str]] = defaultdict(list)

    for email in emails:
        email_id = email["id"]
        slot = SLOTS[email_id]
        where = f"{email_id}"

        body = email["body"]
        if not body.strip():
            problems.append(f"{where}: empty body")
            continue
        if "\\n" in body:
            problems.append(f"{where}: literal backslash-n in body")
        words = len(body.split())
        low, high = BANDS[slot.band]
        if not low <= words <= high:
            problems.append(f"{where}: {words} words, band {slot.band} wants {low}-{high}")
        for line in body.split("\n"):
            line = line.strip()
            if len(line.split()) > SHARED_LINE_MAX_WORDS:
                lines[line].append(email_id)

        for field in ("scenario", "note", "difficulty"):
            if not email[field].strip():
                problems.append(f"{where}: blank {field}")
        if email["difficulty"] not in ("easy", "medium", "hard"):
            problems.append(f"{where}: difficulty {email['difficulty']!r}")

        headers = email["headers"]
        if headers["to"] != INBOX_OWNER:
            problems.append(f"{where}: to is {headers['to']!r}")
        if not headers["from"].strip() or not headers["subject"].strip():
            problems.append(f"{where}: blank from/subject")
        try:
            sent = parsedate_to_datetime(headers["date"])
        except (TypeError, ValueError):
            problems.append(f"{where}: unparseable date {headers['date']!r}")
        else:
            if headers["date"][-5:] != "-0400":
                problems.append(f"{where}: date is not -0400: {headers['date']!r}")
            if sent > now:
                problems.append(f"{where}: sent {headers['date']}, after CURRENT_TIME")

        actions = email["label"]["actions"]
        if actions and email["label"]["next_step"] != "flag_for_human":
            problems.append(f"{where}: actions on a {email['label']['next_step']} email")
        for action in actions:
            if re.search(r"\b(and|or)\b", action["verb"]):
                problems.append(f"{where}: compound verb {action['verb']!r}")
            deadline = action.get("deadline")
            if deadline and not DEADLINE_RE.match(deadline):
                problems.append(f"{where}: deadline {deadline!r} is not absolute")
        if email["label"]["draft"] is not None:
            problems.append(f"{where}: draft is scored out of scope and must stay null")

        returns = email["tool_returns"]
        if returns["check_unknown_sender"]["known"] is not slot.known:
            problems.append(
                f"{where}: check_unknown_sender overwritten (scaffold says {slot.known})"
            )
        if returns["check_calendar_available"]["available"] is not slot.available:
            problems.append(
                f"{where}: check_calendar_available overwritten (scaffold says {slot.available})"
            )
        notes = returns["get_note"]["notes"]
        if len(notes) != slot.notes:
            problems.append(f"{where}: {len(notes)} notes, scaffold says {slot.notes}")
        lengths = [len(note.split()) for note in notes]
        if slot.long_note:
            if not any(LONG_NOTE_MIN_WORDS <= n <= LONG_NOTE_MAX_WORDS for n in lengths):
                problems.append(
                    f"{where}: needs a {LONG_NOTE_MIN_WORDS}-{LONG_NOTE_MAX_WORDS} word artifact, got {lengths}"
                )
        elif any(n > SHORT_NOTE_MAX_WORDS for n in lengths):
            problems.append(
                f"{where}: note longer than {SHORT_NOTE_MAX_WORDS} words but not marked long_note"
            )
        for note in notes:
            if re.search(r"\b(flag|escalate|surface) (this|it)\b", note, re.I):
                problems.append(f"{where}: note instructs the agent, leaking the label")

    for line, owners in lines.items():
        if len(set(owners)) > 1:
            problems.append(f"shared body line across {sorted(set(owners))}: {line[:60]!r}")

    return problems


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


def write_plan(emails: list[dict]) -> None:
    """Per-email authoring targets, as a table to write against."""
    rows = [
        "# Authoring plan for emails_individual.json",
        "",
        "Generated by `datasets/scripts/generate_individual.py`. Every column is a",
        "constraint on the email that goes in the slot, not a description of one.",
        "`--check` verifies the filled dataset against this table.",
        "",
        "`cal` is what the calendar fixture is for: `settles` (its value answers the",
        "ask), `blocks` (its value rules out the easy answer), `irrelevant` (the email",
        "is written so the value changes nothing). Body bands in words: "
        + ", ".join(f"{k} {v[0]}-{v[1]}" for k, v in BANDS.items())
        + ".",
        "",
        "| id | category | next_step | conflict | known | available | cal | notes | body |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for email in emails:
        slot = SLOTS[email["id"]]
        notes = str(slot.notes) + (" (one long)" if slot.long_note else "")
        low, high = BANDS[slot.band]
        rows.append(
            f"| {email['id']} | {email['category']} | {email['label']['next_step']} | "
            f"{'yes' if email['notes_conflict'] else ''} | {slot.known} | {slot.available} | "
            f"{slot.calendar} | {notes} | {slot.band} ({low}-{high}) |"
        )
    PLAN_PATH.write_text("\n".join(rows) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite even if the existing dataset has hand-written bodies.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the existing dataset against the scaffold instead of writing it.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Rewrite the authoring plan only, for constraint edits made after the bodies.",
    )
    args = parser.parse_args()

    assert sum(s.count for s in SEGMENTS) == TOTAL
    assert len(SLOTS) == TOTAL, len(SLOTS)

    emails = scaffold()
    problems = check_balance(emails)
    if problems:
        sys.exit("SLOTS is unbalanced:\n" + "\n".join(f"  - {p}" for p in problems))

    if args.plan:
        write_plan(emails)
        print(f"Wrote the authoring plan to {PLAN_PATH}")
        return

    if args.check:
        if not OUTPUT_PATH.exists():
            sys.exit(f"{OUTPUT_PATH} does not exist; run without --check first.")
        filled = json.loads(OUTPUT_PATH.read_text())
        if [e["id"] for e in filled] != [e["id"] for e in emails]:
            sys.exit("dataset ids do not match the scaffold; regenerate it.")
        for written, scaffolded in zip(filled, emails):
            for field in ("category", "notes_conflict"):
                if written[field] != scaffolded[field]:
                    problems.append(f"{written['id']}: {field} overwritten")
            if written["label"]["next_step"] != scaffolded["label"]["next_step"]:
                problems.append(f"{written['id']}: next_step overwritten")
        problems += check_content(filled)
        if problems:
            sys.exit(f"{len(problems)} problems:\n" + "\n".join(f"  - {p}" for p in problems))
        print(f"OK: {len(filled)} emails match the scaffold and the guideline.")
        return

    already = written_emails(OUTPUT_PATH)
    if already and not args.force:
        sys.exit(
            f"Refusing to overwrite {OUTPUT_PATH}: {already} emails already have bodies.\n"
            "The scaffold is blank, so this would discard them. Re-run with --force if "
            "that is what you want."
        )

    OUTPUT_PATH.write_text(json.dumps(emails, indent=2) + "\n")
    write_plan(emails)

    print(f"Wrote {len(emails)} emails to {OUTPUT_PATH}")
    print(f"Wrote the authoring plan to {PLAN_PATH}")
    print("category:", dict(Counter(e["category"] for e in emails)))
    print("next_step:", dict(Counter(e["label"]["next_step"] for e in emails)))
    print("notes_conflict:", sum(1 for e in emails if e["notes_conflict"]))
    print("known=false:", sum(1 for s in SLOTS.values() if not s.known))
    print("calendar:", dict(Counter(s.calendar for s in SLOTS.values())))
    print("bands:", dict(Counter(s.band for s in SLOTS.values())))


if __name__ == "__main__":
    main()
