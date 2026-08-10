import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full")


@app.cell
def _():
    import json
    from collections import Counter
    from pathlib import Path
    from statistics import median
    from typing import Any

    import marimo as mo

    return Any, Counter, Path, json, median, mo


@app.cell
def _(Any, Path, json, mo):
    # The notebook lives alongside the dataset it reads.
    DATA_PATH: Path = mo.notebook_dir() / "emails_individual.json"
    emails: list[dict[str, Any]] = json.loads(DATA_PATH.read_text())

    # Read tools an email may declare a fixture for. The mock server raises on any
    # read-tool call without one, so this is also the set of calls each email allows.
    READ_TOOLS: tuple[str, ...] = (
        "check_calendar_available",
        "check_unknown_sender",
        "get_note",
    )

    def notes_of(email: dict[str, Any]) -> list[str]:
        return email["tool_returns"].get("get_note", {}).get("notes", [])

    def body_text(email: dict[str, Any]) -> str:
        return email["body"]

    def words(email: dict[str, Any]) -> int:
        return len(body_text(email).split())

    def note_words(email: dict[str, Any]) -> int:
        return sum(len(n.split()) for n in notes_of(email))

    def chars(email: dict[str, Any]) -> int:
        return len(body_text(email))

    def note_chars(email: dict[str, Any]) -> int:
        return sum(len(n) for n in notes_of(email))

    mo.md(f"# `{DATA_PATH.name}` — {len(emails)} emails")
    return (
        READ_TOOLS,
        body_text,
        chars,
        emails,
        note_chars,
        note_words,
        notes_of,
        words,
    )


@app.cell
def _(Any, Counter, emails: "list[dict[str, Any]]", mo):
    # Targets from the README's Dataset section, as a share of the set.
    CATEGORY_TARGETS: dict[str, float] = {
        "promotional": 0.15,
        "fyi": 0.15,
        "single-ask": 0.30,
        "multi-ask": 0.15,
        "buried": 0.15,
        "suspicious": 0.10,
    }
    NEXT_STEPS: tuple[str, ...] = ("reply", "no_action", "flag_for_human")
    DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

    def _bar(n: int, total: int, width: int = 12) -> str:
        return "█" * round(width * n / total) if total else ""

    def crosstab(order: list[str], values: list[dict[str, Any]]) -> str:
        """Category × next_step, with the README share target beside the actual one."""
        counts = Counter((e["category"], e["label"]["next_step"]) for e in values)
        total = len(values)
        head = (
            "| Category | " + " | ".join(f"`{s}`" for s in NEXT_STEPS) + " | n | share | target |"
        )
        rule = "| --- | " + " | ".join("---" for _ in NEXT_STEPS) + " | --- | --- | --- |"
        lines = [head, rule]
        for category in order:
            cells = " | ".join(str(counts[(category, s)] or "·") for s in NEXT_STEPS)
            n = sum(counts[(category, s)] for s in NEXT_STEPS)
            target = CATEGORY_TARGETS.get(category)
            share = f"{n / total:.0%}" if total else "—"
            lines.append(
                f"| `{category}` | {cells} | {n} | {share} | "
                f"{f'{target:.0%}' if target is not None else '—'} |"
            )
        totals = " | ".join(str(sum(counts[(c, s)] for c in order)) for s in NEXT_STEPS)
        lines.append(f"| **all** | {totals} | {total} | 100% | 100% |")
        return "\n".join(lines)

    def tally(key: str, order: tuple[str, ...], values: list[dict[str, Any]]) -> str:
        counts = Counter(e[key] for e in values)
        total = len(values)
        lines = [f"| {key} | n | share | |", "| --- | --- | --- | --- |"]
        for name in order:
            n = counts[name]
            lines.append(f"| `{name}` | {n} | {n / total:.0%} | {_bar(n, total)} |")
        return "\n".join(lines)

    def difficulty_by_route(values: list[dict[str, Any]]) -> str:
        """Where the hard cases sit. A route whose hard cases all live in one
        category is a route the eval only probes one way."""
        counts = Counter((e["label"]["next_step"], e["difficulty"]) for e in values)
        head = "| next_step | " + " | ".join(f"`{d}`" for d in DIFFICULTIES) + " |"
        rule = "| --- | " + " | ".join("---" for _ in DIFFICULTIES) + " |"
        rows = "\n".join(
            f"| `{step}` | "
            + " | ".join(str(counts[(step, d)] or "·") for d in DIFFICULTIES)
            + " |"
            for step in NEXT_STEPS
        )
        return f"{head}\n{rule}\n{rows}"

    categories = list(CATEGORY_TARGETS) + [
        c for c in sorted({e["category"] for e in emails}) if c not in CATEGORY_TARGETS
    ]

    distribution = mo.vstack(
        [
            mo.md(crosstab(categories, emails)),
            mo.md(
                "*`·` is an empty cell. `share` is of the whole set; `target` is the README's "
                "Dataset section. Promotional and half of fyi target `no_action`; multi-ask and "
                "buried split evenly between `reply` and `flag_for_human`.*"
            ),
            mo.hstack(
                [
                    mo.md(tally("difficulty", DIFFICULTIES, emails)),
                    mo.md(difficulty_by_route(emails)),
                ],
                widths="equal",
                gap=2,
                align="start",
            ),
        ],
        gap=0.5,
    )
    mo.accordion({"## Distribution": distribution})
    return (NEXT_STEPS,)


@app.cell
def _(
    Any,
    Counter,
    READ_TOOLS: tuple[str, ...],
    emails: "list[dict[str, Any]]",
    median,
    mo,
    note_words,
    notes_of,
    words,
):
    def size_by_category(values: list[dict[str, Any]]) -> str:
        """Body length against the guideline's 120-750 word range, plus the notes
        the email hangs on `get_note` — together they are what the model must read."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for email in values:
            grouped.setdefault(email["category"], []).append(email)
        lines = [
            "| Category | n | body words (min–median–max) | note words (median–max) | "
            "longest single note | notes (median–max) |",
            "| --- | --- | --- | --- | --- | --- |",
        ]

        def _row(name: str, group: list[dict[str, Any]]) -> str:
            body = sorted(words(e) for e in group)
            per_email = [note_words(e) for e in group]
            counts = [len(notes_of(e)) for e in group]
            # Longest individual note, not the per-email total — a single fat note and
            # a pile of terse ones cost the same context but read very differently.
            longest = max((len(n.split()) for e in group for n in notes_of(e)), default=0)
            return (
                f"| {name} | {len(group)} | {body[0]}–{median(body):.0f}–{body[-1]} | "
                f"{median(per_email):.0f}–{max(per_email)} | {longest} | "
                f"{median(counts):.0f}–{max(counts)} |"
            )

        for category, group in sorted(grouped.items()):
            lines.append(_row(f"`{category}`", group))
        lines.append(_row("**all**", values))
        return "\n".join(lines)

    def heaviest(values: list[dict[str, Any]], n: int = 8) -> str:
        """The emails that set peak context — body plus every note, since a single
        `get_note` call returns the whole list."""
        ranked = sorted(values, key=lambda e: words(e) + note_words(e), reverse=True)[:n]
        lines = [
            "| id | Category | Difficulty | body + notes | notes |",
            "| --- | --- | --- | --- | --- |",
        ]
        for email in ranked:
            lines.append(
                f"| `{email['id']}` | `{email['category']}` | `{email['difficulty']}` | "
                f"{words(email)} + {note_words(email)} = {words(email) + note_words(email)} | "
                f"{len(notes_of(email))} |"
            )
        return "\n".join(lines)

    def fixtures(values: list[dict[str, Any]]) -> str:
        """Which read tools each email allows, and what the fixture returns. A tool
        whose fixture never varies is a tool the dataset does not exercise."""
        lines = ["| Tool | Emails with a fixture | Distinct returns |", "| --- | --- | --- |"]
        for tool in READ_TOOLS:
            present = [e for e in values if tool in e["tool_returns"]]
            if tool == "get_note":
                empty = sum(1 for e in present if not notes_of(e))
                returns = f"{len(present) - empty} with notes, {empty} empty"
            else:
                counts = Counter(str(e["tool_returns"][tool]) for e in present)
                returns = ", ".join(f"`{v}` ×{n}" for v, n in counts.most_common())
            lines.append(f"| `{tool}` | {len(present)}/{len(values)} | {returns} |")
        return "\n".join(lines)

    size = mo.vstack(
        [
            mo.md(size_by_category(emails)),
            mo.md(
                "*The guideline asks for bodies of 120-750 words with the spread deliberately "
                "wide. Notes are counted separately because `get_note` returns the whole list in "
                "one tool result — noise entries land in context whether or not they bear on the "
                "answer.*"
            ),
            mo.hstack(
                [
                    mo.vstack([mo.md("**Heaviest emails**"), mo.md(heaviest(emails))], gap=0.5),
                    mo.vstack(
                        [
                            mo.md("**Tool fixtures**"),
                            mo.md(fixtures(emails)),
                            mo.md(
                                "*The mock server raises on a read-tool call with no fixture, so "
                                "a missing row means that call fails the run.*"
                            ),
                        ],
                        gap=0.5,
                    ),
                ],
                widths="equal",
                gap=2,
                align="start",
            ),
        ],
        gap=0.5,
    )
    mo.accordion({"## Size and fixtures": size})
    return


@app.cell
def _(
    Any,
    chars,
    emails: "list[dict[str, Any]]",
    median,
    mo,
    note_chars,
    notes_of,
):
    # Characters are the unit the context window is actually spent in. ~4 chars per
    # token is the usual English rule of thumb; it is a rough divisor, not a
    # tokenizer, so treat the token columns as an order of magnitude.
    CHARS_PER_TOKEN = 4

    def _tokens(n: int) -> str:
        return f"~{n / CHARS_PER_TOKEN:,.0f}"

    def char_totals(values: list[dict[str, Any]]) -> str:
        """What one pass over the dataset costs, split by what carries it. The
        prompt also pays for the envelope and the system/skill text, which this
        does not count — these are the dataset's own contribution."""
        body = sum(chars(e) for e in values)
        notes = sum(note_chars(e) for e in values)
        lines = [
            "| Source | Characters | Est. tokens | Share |",
            "| --- | --- | --- | --- |",
            f"| bodies | {body:,} | {_tokens(body)} | {body / (body + notes):.0%} |",
            f"| `get_note` returns | {notes:,} | {_tokens(notes)} | "
            f"{notes / (body + notes):.0%} |",
            f"| **total** | **{body + notes:,}** | **{_tokens(body + notes)}** | 100% |",
        ]
        return "\n".join(lines)

    def chars_by_category(values: list[dict[str, Any]]) -> str:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for email in values:
            grouped.setdefault(email["category"], []).append(email)
        lines = [
            "| Category | n | body chars (min–median–max) | note chars (median) | "
            "category total | est. tokens |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for category, group in sorted(grouped.items()):
            body = sorted(chars(e) for e in group)
            total = sum(chars(e) + note_chars(e) for e in group)
            lines.append(
                f"| `{category}` | {len(group)} | {body[0]:,}–{median(body):,.0f}–{body[-1]:,} | "
                f"{median([note_chars(e) for e in group]):,.0f} | {total:,} | {_tokens(total)} |"
            )
        body = sorted(chars(e) for e in values)
        total = sum(chars(e) + note_chars(e) for e in values)
        lines.append(
            f"| **all** | {len(values)} | {body[0]:,}–{median(body):,.0f}–{body[-1]:,} | "
            f"{median([note_chars(e) for e in values]):,.0f} | {total:,} | {_tokens(total)} |"
        )
        return "\n".join(lines)

    def char_spread(values: list[dict[str, Any]], buckets: int = 8) -> str:
        """Body-length histogram. The point of the dataset is that this is wide and
        not clustered, so a single tall bucket is a regression."""
        sizes = [chars(e) for e in values]
        low, high = min(sizes), max(sizes)
        width = (high - low) / buckets
        lines = ["| Body chars | n | |", "| --- | --- | --- |"]
        for i in range(buckets):
            start = low + i * width
            end = start + width
            # Last bucket is closed so the longest email lands somewhere.
            in_bucket = [s for s in sizes if (start <= s < end or (i == buckets - 1 and s == high))]
            lines.append(
                f"| {start:,.0f}–{end:,.0f} | {len(in_bucket) or ''} | " f"{'█' * len(in_bucket)} |"
            )
        return "\n".join(lines)

    def char_extremes(values: list[dict[str, Any]], n: int = 5) -> str:
        ranked = sorted(values, key=lambda e: chars(e) + note_chars(e))
        rows = [("shortest", ranked[:n]), ("longest", list(reversed(ranked[-n:])))]
        lines = [
            "| | id | Category | body + notes | est. tokens |",
            "| --- | --- | --- | --- | --- |",
        ]
        for name, group in rows:
            for i, email in enumerate(group):
                total = chars(email) + note_chars(email)
                lines.append(
                    f"| {name if i == 0 else ''} | `{email['id']}` | `{email['category']}` | "
                    f"{chars(email):,} + {note_chars(email):,} = {total:,} | {_tokens(total)} |"
                )
        return "\n".join(lines)

    characters = mo.vstack(
        [
            mo.md(char_totals(emails)),
            mo.md(
                f"*Estimated at {CHARS_PER_TOKEN} characters per token — a rule of thumb, not a "
                "tokenizer. Use it to size a run, not to predict `tokens_in`. Notes count once "
                "here, but a ReAct loop re-sends them on every subsequent turn, which is the gap "
                "`tokens_in_cumulative` measures against `tokens_in_unique`.*"
            ),
            mo.md(chars_by_category(emails)),
            mo.hstack(
                [
                    mo.vstack(
                        [
                            mo.md("**Body-length spread**"),
                            mo.md(char_spread(emails)),
                            mo.md(
                                "*Length is meant to be spread wide and uncorrelated with "
                                "category, so one dominant bucket is a regression.*"
                            ),
                        ],
                        gap=0.5,
                    ),
                    mo.vstack(
                        [
                            mo.md("**Extremes**"),
                            mo.md(char_extremes(emails)),
                            mo.md(
                                f"*The longest email plus its notes is what one graph node must "
                                f"hold at once; a loop's peak grows past it with every turn. "
                                f"Emails with no `get_note` fixture show 0 notes — "
                                f"{sum(1 for e in emails if not notes_of(e))} of "
                                f"{len(emails)} carry none.*"
                            ),
                        ],
                        gap=0.5,
                    ),
                ],
                widths="equal",
                gap=2,
                align="start",
            ),
        ],
        gap=0.5,
    )
    mo.accordion({"## Character counts": characters})
    return


@app.cell
def _(Any, Counter, emails: "list[dict[str, Any]]", mo):
    def action_rows(values: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        return [(e["id"], a) for e in values for a in e["label"]["actions"]]

    def verbs(values: list[dict[str, Any]]) -> str:
        counts = Counter(a["verb"] for _, a in action_rows(values))
        lines = ["| Verb | n |", "| --- | --- |"]
        lines += [f"| `{verb}` | {n} |" for verb, n in counts.most_common()]
        return "\n".join(lines)

    def deadlines(values: list[dict[str, Any]]) -> str:
        """Deadline shapes, since scoring only asks whether a prediction carries one
        that loosely agrees — a mix of absolute and relative wording is the hard part."""
        rows = action_rows(values)
        absolute = sum(1 for _, a in rows if a.get("deadline") and "20" in a["deadline"])
        relative = sum(1 for _, a in rows if a.get("deadline") and "20" not in a["deadline"])
        missing = sum(1 for _, a in rows if not a.get("deadline"))
        lines = ["| Deadline | n |", "| --- | --- |"]
        for name, n in (
            ("absolute date", absolute),
            ("relative wording", relative),
            ("none", missing),
        ):
            lines.append(f"| {name} | {n} |")
        return "\n".join(lines)

    def action_list(values: list[dict[str, Any]]) -> str:
        lines = ["| id | Verb | Subject | Deadline |", "| --- | --- | --- | --- |"]
        for email_id, action in action_rows(values):
            lines.append(
                f"| `{email_id}` | {action['verb']} | {action['subject']} | "
                f"{action.get('deadline') or '—'} |"
            )
        return "\n".join(lines)

    flagged = [e for e in emails if e["label"]["next_step"] == "flag_for_human"]
    with_actions = [e for e in flagged if e["label"]["actions"]]

    actions_view = mo.vstack(
        [
            mo.md(
                f"{len(action_rows(emails))} label actions across {len(with_actions)} emails. "
                f"{len(flagged) - len(with_actions)} of the {len(flagged)} `flag_for_human` "
                "emails carry none, so any action a model emits on those scores as a false "
                "positive."
            ),
            mo.hstack(
                [mo.md(verbs(emails)), mo.md(deadlines(emails))],
                widths="equal",
                gap=2,
                align="start",
            ),
            mo.accordion({"Every action": mo.md(action_list(emails))}),
        ],
        gap=0.5,
    )
    mo.accordion({"## Actions": actions_view})
    return


@app.cell
def _(NEXT_STEPS: tuple[str, ...], emails: "list[dict[str, Any]]", mo):
    category_filter = mo.ui.multiselect(
        options=sorted({e["category"] for e in emails}), label="Category"
    )
    route_filter = mo.ui.multiselect(options=list(NEXT_STEPS), label="next_step")
    difficulty_filter = mo.ui.multiselect(
        options=sorted({e["difficulty"] for e in emails}), label="Difficulty"
    )
    search = mo.ui.text(label="Search", placeholder="id, subject, body, note")

    mo.hstack([category_filter, route_filter, difficulty_filter, search], gap=1, align="end")
    return category_filter, difficulty_filter, route_filter, search


@app.cell
def _(Any, body_text, mo, notes_of):
    ROUTE_MARK: dict[str, str] = {
        "reply": "✉",
        "no_action": "·",
        "flag_for_human": "⚑",
    }

    def matches(email: dict[str, Any], needle: str) -> bool:
        if not needle:
            return True
        haystack = " ".join(
            [email["id"], email["headers"]["subject"], email["body"], *notes_of(email)]
        )
        return needle.lower() in haystack.lower()

    def header(email: dict[str, Any]) -> str:
        subject = email["headers"]["subject"] or "(no subject)"
        notes = notes_of(email)
        return (
            f"{ROUTE_MARK[email['label']['next_step']]} `{email['id']}` · {subject} · "
            f"**{email['label']['next_step']}** · `{email['category']}` / "
            f"`{email['difficulty']}` · {len(body_text(email).split())}w"
            + (f" + {len(notes)} notes" if notes else "")
        )

    def body_md(email: dict[str, Any]) -> str:
        """Keep single line breaks visible — markdown would otherwise fold a wrapped
        thread into one line."""
        return body_text(email).replace("\n", "  \n")

    def notes_md(email: dict[str, Any]) -> str:
        notes = notes_of(email)
        return "\n".join(f"- {n}" for n in notes) or "*No notes.*"

    def label_md(email: dict[str, Any]) -> str:
        label = email["label"]
        actions = (
            "\n".join(
                f"- **{a['verb']}** — {a['subject']}"
                + (f" *(by {a['deadline']})*" if a.get("deadline") else "")
                for a in label["actions"]
            )
            or "*No actions.*"
        )
        return f"**next_step** `{label['next_step']}`\n\n{actions}"

    def detail(email: dict[str, Any]) -> Any:
        return mo.vstack(
            [
                mo.md(
                    f"**From** {email['headers']['from']}  \n"
                    f"**To** {email['headers']['to']}"
                    + (f"  \n**Cc** {email['headers']['cc']}" if email["headers"]["cc"] else "")
                    + f"  \n**Date** {email['headers']['date']}"
                ),
                mo.hstack(
                    [
                        mo.vstack([mo.md("**Body**"), mo.md(body_md(email))], gap=0.5),
                        mo.vstack(
                            [
                                mo.md("**Label**"),
                                mo.md(label_md(email)),
                                mo.md("**`get_note` returns**"),
                                mo.md(notes_md(email)),
                                mo.md("**Why**"),
                                mo.md(f"*{email['note']}*" if email["note"] else "*—*"),
                                mo.accordion(
                                    {
                                        "All tool returns": mo.lazy(
                                            lambda e=email: mo.json(e["tool_returns"])
                                        )
                                    }
                                ),
                            ],
                            gap=0.5,
                        ),
                    ],
                    widths=[2, 1],
                    gap=2,
                    align="start",
                ),
            ],
            gap=0.5,
        )

    return detail, header, matches


@app.cell
def _(
    category_filter,
    detail,
    difficulty_filter,
    emails: "list[dict[str, Any]]",
    header,
    matches,
    mo,
    route_filter,
    search,
):
    selected = [
        e
        for e in emails
        if (not category_filter.value or e["category"] in category_filter.value)
        and (not route_filter.value or e["label"]["next_step"] in route_filter.value)
        and (not difficulty_filter.value or e["difficulty"] in difficulty_filter.value)
        and matches(e, search.value)
    ]

    mo.vstack(
        [
            mo.md(f"### {len(selected)}/{len(emails)} emails"),
            (
                mo.accordion({header(e): mo.lazy(lambda email=e: detail(email)) for e in selected})
                if selected
                else mo.callout(mo.md("Nothing matches those filters."), kind="warn")
            ),
        ],
        gap=0.5,
    )
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
