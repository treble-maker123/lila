import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full")


@app.cell
def _():
    import json
    from collections import Counter
    from pathlib import Path
    from statistics import mean
    from typing import Any

    import marimo as mo

    return Any, Counter, Path, json, mean, mo


@app.cell
def _(mo):
    # The notebook lives in results/, so the datasets sit one level up.
    RESULTS_DIR = mo.notebook_dir()
    DATA_PATH = RESULTS_DIR.parent / "datasets" / "emails_individual.json"

    files = sorted(
        RESULTS_DIR.glob("emails_individual*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    picker = mo.ui.dropdown(
        options={p.name: str(p) for p in files},
        value=files[0].name if files else None,
        label="Results file",
    )
    picker
    return DATA_PATH, picker


@app.cell
def _(DATA_PATH, Path, json, mo, picker):
    mo.stop(not picker.value, mo.callout(mo.md("No results file found."), kind="warn"))

    payload = json.loads(Path(picker.value).read_text())
    # Gold labels and email metadata, keyed by id.
    gold = {e["id"]: e for e in json.loads(DATA_PATH.read_text())}
    # Setup id -> display name, from the scored labels block when it is present.
    names = {str(m["setup"]): m["label"] for m in payload.get("labels", [])}
    return gold, names, payload


@app.cell
def _(Any, mo, names, payload):
    # Counts and caveats copied from the README's Cost table, so reading the numbers
    # does not mean jumping back to the doc.
    COST_NOTES: dict[str, tuple[str, str]] = {
        "tokens_in_cumulative": (
            "every call's prompt, summed",
            "A loop re-sends its conversation each turn, so an N-turn loop counts the shared "
            "prefix N times. What an uncached, per-call-billed API charges; **overstates** the "
            "loop locally, where the KV cache avoids the re-reading",
        ),
        "tokens_in_unique": (
            "each prompt token once",
            "For the loop, just the final call's prompt — the message list only grows, so every "
            "earlier prompt is a prefix of it. For the graph the node prompts share no cacheable "
            "prefix, so it equals cumulative. **Understates** the loop",
        ),
        "input-token gap": (
            "`cumulative − unique`",
            "The loop's re-reading overhead. Zero for the graph, whose nodes share no prefix",
        ),
        "tokens_out": ("generated tokens", "—"),
        "wall_clock_ms": (
            "time in the scored region",
            "Rough. The model is warmed up first so load time doesn't land on email #1 alone "
            "(`--no-warm-up` to disable)",
        ),
        "peak_context_tokens": (
            "KV occupancy in tokens: `max` over calls of prompt + generated",
            "What must be resident at once, not what was processed. The loop's grows with its "
            "transcript; the graph's is its largest single node",
        ),
        "memory": (
            "that peak in bytes: `kv_bytes` + `weights_bytes` = `total_bytes`",
            "Weights are equal across setups, so the difference is all `kv_bytes`. Null if "
            "calibration failed",
        ),
    }

    def _value(summary: dict[str, Any], metric: str) -> int | None:
        if metric == "input-token gap":
            return summary["tokens_in_cumulative"] - summary["tokens_in_unique"]
        if metric == "memory":
            return summary["memory"]["total_bytes"] if summary["memory"] else None
        return summary[metric]

    def _display(summary: dict[str, Any], metric: str) -> str:
        value = _value(summary, metric)
        if value is None:
            return "—"
        if metric == "memory":
            memory = summary["memory"]
            return f"{value / 1e6:,.0f} MB ({memory['kv_bytes'] / 1e6:,.0f} MB KV)"
        if metric == "wall_clock_ms":
            return f"{value:,} ms ({value / 1000:.1f} s)"
        return f"{value:,}"

    def cost_table(summaries: list[dict[str, Any]]) -> str:
        """One row per metric, one column per setup, plus a ratio against the first."""
        baseline = summaries[0]
        head = " | ".join(names.get(str(s["setup"]), s["label"]) for s in summaries)
        lines = [
            f"| Metric | {head} | vs {names.get(str(baseline['setup']), baseline['label'])} |",
            "| --- | " + " | ".join("---" for _ in summaries) + " | --- |",
        ]
        for metric in COST_NOTES:
            cells = " | ".join(_display(s, metric) for s in summaries)
            base, last = _value(baseline, metric), _value(summaries[-1], metric)
            ratio = (
                f"×{last / base:.2f}" if base and last is not None and len(summaries) > 1 else "—"
            )
            lines.append(f"| `{metric}` | {cells} | {ratio} |")
        return "\n".join(lines)

    summaries = sorted(payload.get("summary", []), key=lambda s: s["setup"])
    notes = "\n".join(
        f"| `{k}` | {counts} | {caveat} |" for k, (counts, caveat) in COST_NOTES.items()
    )

    cost_view = mo.vstack(
        [
            mo.md(cost_table(summaries)) if summaries else mo.md("*No `summary` block.*"),
            mo.md(
                "*Tokens and time sum across every email of every run; `peak_context_tokens` "
                "and `memory` take the **max** — a setup needs its worst email, not the total. "
                "The ratio column compares the last setup to the first.*"
            ),
            mo.accordion(
                {
                    "What these count": mo.md(
                        f"| Metric | Counts | Caveat |\n| --- | --- | --- |\n{notes}\n\n"
                        "Report both input-token numbers; the gap between them is the loop's "
                        "re-reading overhead."
                    )
                }
            ),
        ],
        gap=0.5,
    )
    mo.accordion({"## Cost": cost_view})
    return


@app.cell
def _(Any, payload):
    labels = sorted(payload.get("labels", []), key=lambda m: m["setup"])

    def _spread(dist: dict[str, Any]) -> str:
        """mean, with the run-to-run spread only when there is more than one run."""
        if len(dist["values"]) < 2:
            return f"{dist['mean']:.0f}"
        return f"{dist['mean']:.1f} ({dist['minimum']}–{dist['maximum']}, sd {dist['stdev']:.1f})"

    def _pct(numerator: float, denominator: float) -> str:
        return f"{100 * numerator / denominator:.1f}%" if denominator else "—"

    def headline_table(metrics: list[dict[str, Any]]) -> str:
        rows: list[tuple[str, list[str]]] = [
            ("emails × runs", [f"{m['emails']} × {m['runs']}" for m in metrics]),
            ("correct", [_spread(m["correct"]) for m in metrics]),
            ("correct / emails", [_pct(m["correct"]["mean"], m["emails"]) for m in metrics]),
            (
                "correct / decided",
                [_pct(m["correct"]["mean"], m["decided"]["mean"]) for m in metrics],
            ),
            ("decided", [_spread(m["decided"]) for m in metrics]),
            ("majority_correct", [f"{m['majority_correct']}/{m['emails']}" for m in metrics]),
            ("error rate", [_pct(m["errors"]["total"], m["email_runs"]) for m in metrics]),
        ]
        head = " | ".join(m["label"] for m in metrics)
        body = "\n".join(f"| `{name}` | {' | '.join(cells)} |" for name, cells in rows)
        return f"| Metric | {head} |\n| --- | {' | '.join('---' for _ in metrics)} |\n{body}"

    def pass_table(metrics: list[dict[str, Any]]) -> str:
        """pass^k as a k-vs-value curve, one column per setup."""
        ks = sorted({p["k"] for m in metrics for p in m["pass_curve"]})
        head = " | ".join(m["label"] for m in metrics)
        lines = [f"| k | {head} |", f"| --- | {' | '.join('---' for _ in metrics)} |"]
        for k in ks:
            cells = []
            for m in metrics:
                point = next((p for p in m["pass_curve"] if p["k"] == k), None)
                cells.append(f"{point['value']:.3f}" if point else "—")
            lines.append(f"| {k} | {' | '.join(cells)} |")
        return "\n".join(lines)

    return headline_table, labels, pass_table


@app.cell
def _(headline_table, labels, mo, pass_table):
    mo.stop(not labels, mo.callout(mo.md("No `labels` block — run `make score`."), kind="warn"))

    labels_headline = mo.vstack(
        [
            mo.md(headline_table(labels)),
            mo.md(
                "*Both `correct / emails` and `correct / decided` are worth reading: the first "
                "is the headline, the second separates routing quality from failing to "
                "terminate. An `error` is never correct.*"
            ),
            mo.md("**pass^k** — all k of k runs correct"),
            mo.md(pass_table(labels)),
            mo.md(
                "*`pass^1` is exactly the mean of `correct / emails`, so the decay from it is "
                "instability, not accuracy. Needs `--runs` ≥ 2 to say anything.*"
            ),
            mo.accordion({"What these count": mo.md("""
    | Metric | Calculation | Across runs |
    | --- | --- | --- |
    | `correct` | per run, over all emails; an `error` is never correct | mean / min / max / stdev |
    | `majority_correct` | per email, whether the most common answer across runs matches the label | single number |
    | `decided` | per run; emails − errors. Denominator for routing quality with robustness factored out | mean / min / max / stdev |
    | `tp` / `fp` / `fn` / `tn` | per run, per class, one-vs-rest; `error` predictions count against every class | summed |
    | `pass^k` | per email, how many of the runs matched the label | curve over k = 1…runs |

    `correct` and `pass^k` are two margins of one emails × runs matrix: `correct` reads a
    row (one run, every email), `pass^k` a column (one email, right *all* k times —
    τ-bench's sense, not best-of-k), estimated over `n` runs with `cᵢ` correct on email *i*
    as `pass^k = (1/emails) · Σᵢ C(cᵢ, k) / C(n, k)`.

    `majority_correct` is the opposite bound — `pass^n` is the setup unaided, majority is it
    with n-way voting on top. An email right in 2 of 3 runs gives 0.67 to mean `correct`, 0
    to `pass^3`, 1 to `majority_correct`.

    At 40 emails gaps under ~8 points are noise, and `pass^k` is the noisiest here.
                            """)}),
        ],
        gap=0.5,
    )
    return (labels_headline,)


@app.cell
def _(Any, labels, mo):
    CLASS_COLUMNS = ("tp", "fp", "fn", "tn", "precision", "recall", "f1")

    def _cells(counts: dict[str, Any]) -> str:
        return " | ".join(
            f"{counts[c]:.2f}" if isinstance(counts[c], float) else str(counts[c])
            for c in CLASS_COLUMNS
        )

    def class_table(metrics: dict[str, Any]) -> str:
        head = "| Class | " + " | ".join(CLASS_COLUMNS) + " |"
        rule = "| --- | " + " | ".join("---" for _ in CLASS_COLUMNS) + " |"
        rows = "\n".join(f"| `{cls}` | {_cells(c)} |" for cls, c in metrics["by_next_step"].items())
        return f"{head}\n{rule}\n{rows}"

    def category_table(metrics: dict[str, Any]) -> str:
        """Same counts sliced by category. Classes a category never exercises are all
        `tn` and say nothing about that email shape, so they are dropped."""
        head = "| Category | Class | " + " | ".join(CLASS_COLUMNS) + " |"
        rule = "| --- | --- | " + " | ".join("---" for _ in CLASS_COLUMNS) + " |"
        rows = "\n".join(
            f"| `{category}` | `{cls}` | {_cells(c)} |"
            for category, slice_ in sorted(metrics["by_category"].items())
            for cls, c in slice_.items()
            if c["tp"] or c["fp"] or c["fn"]
        )
        return f"{head}\n{rule}\n{rows}"

    labels_counts = mo.vstack(
        [
            mo.md("### Counts by class and category"),
            mo.md(
                "*Counted on two axes: per `next_step` class (is `no_action` over-emitted? are "
                "`flag_for_human` emails buried?) and per `category`, which shows the email "
                "shapes that break without needing new labels. Summed across runs.*"
            ),
            mo.hstack(
                [
                    mo.vstack(
                        [
                            mo.md(f"**{m['label']}** — by `next_step`"),
                            mo.md(class_table(m)),
                            mo.accordion({"By `category`": mo.md(category_table(m))}),
                        ],
                        gap=0.5,
                    )
                    for m in labels
                ],
                widths="equal",
                gap=2,
                align="start",
            ),
        ],
        gap=0.5,
    )
    return (labels_counts,)


@app.cell
def _(labels, mo):
    def errors_table() -> str:
        kinds = sorted({k for m in labels for k in m["errors"]["by_kind"]})
        head = " | ".join(m["label"] for m in labels)
        lines = [f"| | {head} |", f"| --- | {' | '.join('---' for _ in labels)} |"]
        lines.append(
            "| total | "
            + " | ".join(f"{m['errors']['total']} ({100 * m['error_rate']:.1f}%)" for m in labels)
            + " |"
        )
        for kind in kinds:
            cells = " | ".join(str(m["errors"]["by_kind"].get(kind, 0)) for m in labels)
            lines.append(f"| `{kind}` | {cells} |")
        return "\n".join(lines)

    labels_errors = mo.vstack(
        [
            mo.md("### Errors"),
            mo.md(errors_table()),
            mo.md(
                "*Email-runs that produced no routing decision. Never folded into `no_action`: "
                "that route is correct for ~22% of the dataset, so defaulting failures to it "
                "would credit whichever setup flails most.*"
            ),
        ],
        gap=0.5,
    )
    return (labels_errors,)


@app.cell
def _(labels_counts, labels_errors, labels_headline, mo):
    mo.accordion({"## Labels": mo.vstack([labels_headline, labels_counts, labels_errors], gap=1.5)})
    return


@app.cell
def _(Any, Counter, mean, mo):
    def _fmt(n: float) -> str:
        """Compact token counts so they fit an accordion header."""
        return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"

    def header(results: list[dict[str, Any]], email: dict[str, Any]) -> str:
        """One line per email, summarising every run of it: how often it routed
        correctly, what it answered, and what it cost on average."""
        runs = len(results)
        expected = email["label"]["next_step"]
        votes = Counter(r["predicted"]["next_step"] for r in results)
        hits = votes[expected]
        mark = "✓" if hits == runs else "✗" if hits == 0 else "~"
        score = f"{mark}" if runs == 1 else f"{mark} {hits}/{runs}"
        # Most common answer first; ×n only when the runs disagree.
        answers = " / ".join(
            f"**{step}**" + (f" ×{n}" if runs > 1 else "") for step, n in votes.most_common()
        )
        tokens_in = mean(r["metrics"]["tokens_in_cumulative"] for r in results)
        tokens_out = mean(r["metrics"]["tokens_out"] for r in results)
        subject = email["headers"]["subject"] or "(no subject)"
        return (
            f"{score} `{results[0]['email_id']}` · {subject} · {answers} · "
            f"{_fmt(tokens_in)} in / {_fmt(tokens_out)} out"
        )

    def body_md(raw: str) -> str:
        """Keep single line breaks visible — markdown would otherwise fold a wrapped
        thread into one line."""
        return raw.replace("\n", "  \n")

    def metrics_md(metrics: dict[str, Any]) -> str:
        rows = [
            ("tokens_in_cumulative", f"{metrics['tokens_in_cumulative']:,}"),
            ("tokens_in_unique", f"{metrics['tokens_in_unique']:,}"),
            ("tokens_out", f"{metrics['tokens_out']:,}"),
            ("wall_clock_ms", f"{metrics['wall_clock_ms']:,}"),
            ("steps", str(metrics["steps"])),
            ("prompt_tokens", ", ".join(str(t) for t in metrics["prompt_tokens"])),
            ("peak_context_tokens", f"{metrics['peak_context_tokens']:,}"),
        ]
        if metrics.get("memory"):
            memory = metrics["memory"]
            rows.append(
                (
                    "memory (total / kv)",
                    f"{memory['total_bytes'] / 1e6:,.0f} MB / {memory['kv_bytes'] / 1e6:,.0f} MB",
                )
            )
        body = "\n".join(f"| `{k}` | {v} |" for k, v in rows)
        return f"| metric | value |\n| --- | --- |\n{body}"

    def actions_md(items: list[dict[str, Any]]) -> str:
        return (
            "; ".join(
                f"{a['verb']} — {a['subject']}"
                + (f" (by {a['deadline']})" if a.get("deadline") else "")
                for a in items
            )
            or "—"
        )

    def outcome_md(results: list[dict[str, Any]], email: dict[str, Any]) -> str:
        """The label, then what each run answered — so disagreement across runs is
        visible without opening a single run."""
        label = email["label"]
        lines = [
            "| | next_step | actions |",
            "| --- | --- | --- |",
            f"| **label** | **{label['next_step']}** | {actions_md(label['actions'])} |",
        ]
        for i, result in enumerate(results):
            predicted = result["predicted"]
            mark = "✓" if predicted["next_step"] == label["next_step"] else "✗"
            lines.append(
                f"| run {i + 1} {mark} | {predicted['next_step']} | "
                f"{actions_md(predicted['actions'])} |"
            )
        return "\n".join(lines)

    def debug_steps(result: dict[str, Any]) -> dict[str, Any]:
        """The setup's trace, one accordion entry per step. A ReAct loop records
        iterations, the graph records nodes — whichever list is populated is used."""
        debug = result["debug"]
        steps = {f"loop {i + 1}": step for i, step in enumerate(debug["loops"])} | {
            f"node {i + 1} · {step['node']}": step for i, step in enumerate(debug["nodes"])
        }
        return {name: mo.lazy(lambda s=step: mo.json(s)) for name, step in steps.items()}

    return body_md, debug_steps, header, metrics_md, outcome_md


@app.cell
def _(Any, body_md, debug_steps, metrics_md, mo, outcome_md):
    def run_detail(result: dict[str, Any]) -> Any:
        """One run of one email: its cost, its draft, and its own debug trace."""
        blocks: list[Any] = [mo.md(metrics_md(result["metrics"]))]
        if result["predicted"]["draft"]:
            blocks.append(mo.accordion({"Draft reply": mo.md(result["predicted"]["draft"])}))
        if result["error"]:
            blocks.append(
                mo.callout(
                    mo.md(f"**{result['error']['kind']}** — {result['error']['detail']}"),
                    kind="danger",
                )
            )
        for warning in result["warnings"]:
            blocks.append(
                mo.callout(mo.md(f"**{warning['kind']}** — {warning['detail']}"), kind="warn")
            )
        blocks.append(
            mo.accordion({"Debug trace": mo.lazy(lambda: mo.accordion(debug_steps(result)))})
        )
        return mo.vstack(blocks, gap=0.5)

    def detail(results: list[dict[str, Any]], email: dict[str, Any]) -> Any:
        """Everything known about one email. What belongs to the email — its envelope,
        body, fixtures and label — is shown once at the top; what belongs to a run —
        metrics, draft, debug trace — is one fold per run below it."""
        runs = {
            f"run {i + 1} · {r['predicted']['next_step']}": mo.lazy(
                lambda result=r: run_detail(result)
            )
            for i, r in enumerate(results)
        }
        return mo.vstack(
            [
                mo.md(
                    f"**From** {email['headers']['from']}  \n"
                    f"**Date** {email['headers']['date']}  \n"
                    f"**Category** `{email['category'] or '—'}`"
                    + (f" · **Difficulty** `{email['difficulty']}`" if email["difficulty"] else "")
                    + (f"  \n**Note** {email['note']}" if email["note"] else "")
                ),
                mo.accordion(
                    {
                        "Email body": mo.md(body_md(email["body"])),
                        # The fixtures the mock server hands back for this email, so a
                        # surprising route can be checked against what the tools said.
                        "Tool returns": mo.lazy(lambda: mo.json(email["tool_returns"])),
                    }
                ),
                mo.md(outcome_md(results, email)),
                mo.accordion(runs),
            ],
            gap=0.5,
        )

    return (detail,)


@app.cell
def _(Any, detail, gold, header, mo, names, payload):
    def by_email(setup: str) -> dict[str, list[dict[str, Any]]]:
        """Every run's result for each email, keyed by email id in dataset order."""
        runs = payload["results"][setup]
        grouped: dict[str, list[dict[str, Any]]] = {r["email_id"]: [] for r in runs[0]}
        for run in runs:
            for result in run:
                grouped[result["email_id"]].append(result)
        return grouped

    def section(setup: str) -> Any:
        """One column: every email of one setup, each row covering all of its runs."""
        grouped = by_email(setup)
        n_runs = len(payload["results"][setup])
        correct = sum(
            1
            for email_id, results in grouped.items()
            for r in results
            if r["predicted"]["next_step"] == gold[email_id]["label"]["next_step"]
        )
        rows = {
            header(results, gold[email_id]): mo.lazy(
                lambda rs=results, eid=email_id: detail(rs, gold[eid])
            )
            for email_id, results in grouped.items()
        }
        tally = (
            f"*{correct}/{len(grouped)} correct*"
            if n_runs == 1
            else f"*{correct / n_runs:.1f}/{len(grouped)} correct, mean over {n_runs} runs*"
        )
        return mo.vstack(
            [
                mo.md(f"### results.{setup} — {names.get(setup, 'setup ' + setup)}"),
                mo.md(tally),
                mo.accordion(rows),
            ],
            gap=0.5,
        )

    def deep_dive() -> Any:
        return mo.hstack(
            [section(setup) for setup in sorted(payload["results"], key=int)],
            widths="equal",
            gap=2,
            align="start",
        )

    # lazy: the columns and their per-email rows are only built when the fold is opened.
    mo.accordion({"## Deep dive": mo.lazy(deep_dive)})
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
