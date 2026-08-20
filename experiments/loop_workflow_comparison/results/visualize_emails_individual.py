import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full")


@app.cell
def _():
    import json
    from collections import Counter
    from math import exp, log, sqrt
    from pathlib import Path
    from statistics import mean, median
    from typing import Any

    import marimo as mo
    from scipy.stats import binomtest, wilcoxon

    return (
        Any,
        Counter,
        Path,
        binomtest,
        exp,
        json,
        log,
        mean,
        median,
        mo,
        sqrt,
        wilcoxon,
    )


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
            "earlier prompt is a prefix of it. The graph's two node prompts lead with the same "
            "email block, which is counted once. **Understates** the loop",
        ),
        "input-token gap": (
            "`cumulative − unique`",
            "Input a perfect prefix cache would not re-evaluate: the loop's re-sent transcript, "
            "the graph's shared email block",
        ),
        "tokens_out": ("generated tokens", "—"),
        "tokens_out_pre": (
            "prose generated before the tool call",
            "The only generated tokens that can steer the route — the model conditions its "
            "choice on them. A loop writes these; a graph node emits the tool call with no "
            "prose at all",
        ),
        "tokens_out_post": (
            "the tool call itself: name and arguments",
            "Draft, actions or reason. Emitted after the tool name, so it cannot change the "
            "route — a cost, not a decision. Estimated when one call mixed prose and a tool "
            "call (`tokens_out_split`)",
        ),
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

    ROLE_ORDER = ("fetch", "gather", "decide", "none")

    def role_table(summaries: list[dict[str, Any]]) -> str:
        """Input tokens grouped by what the call was for. The graph's roles are fixed by
        its nodes; the loop's are inferred from the tools it chose."""
        head = " | ".join(names.get(str(s["setup"]), s["label"]) for s in summaries)
        lines = [
            f"| Call role | {head} |",
            "| --- | " + " | ".join("---" for _ in summaries) + " |",
        ]
        for role in ROLE_ORDER:
            if not any(s.get("calls_by_role", {}).get(role) for s in summaries):
                continue
            cells = " | ".join(
                f"{s.get('tokens_in_by_role', {}).get(role, 0):,} "
                f"({s.get('calls_by_role', {}).get(role, 0)} calls)"
                for s in summaries
            )
            lines.append(f"| `{role}` | {cells} |")
        return "\n".join(lines)

    summaries = sorted(payload.get("summary", []), key=lambda s: s["setup"])
    notes = "\n".join(
        f"| `{k}` | {counts} | {caveat} |" for k, (counts, caveat) in COST_NOTES.items()
    )

    cost_view = mo.vstack(
        [
            mo.md(cost_table(summaries)) if summaries else mo.md("*No `summary` block.*"),
            mo.md("**Input tokens by call role**"),
            (
                mo.md(role_table(summaries))
                if any(s.get("calls_by_role") for s in summaries)
                else mo.md("*This results file predates call-role bucketing; re-run to populate.*")
            ),
            mo.md(
                "*`fetch` is free for the graph — it dispatches `get_new_email` in code — so "
                "the headline input-token gap shrinks a lot once that row is set aside. Note "
                "the call counts: excluding fetch, the graph can make more calls than the loop "
                "and still spend fewer tokens, because its prompts neither carry seven tool "
                "schemas nor re-send a transcript.*"
            ),
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
def _(Any, binomtest, labels):
    def majority_by_email(m: dict[str, Any]) -> dict[str, bool]:
        """One bool per email: correct on a strict majority of runs; a tie is not
        correct. The voting bound — pass^1 with n-way voting on top."""
        return {eid: 2 * n > m["runs"] for eid, n in m["successes"].items()}

    def unanimous_by_email(m: dict[str, Any]) -> dict[str, bool]:
        """One bool per email: correct on *every* run. This is pass^k at k = n, the
        only k whose per-email value is binary — for k < n it is C(cᵢ,k)/C(n,k), an
        average over all k-subsets of the runs, which McNemar cannot take."""
        return {eid: n == m["runs"] for eid, n in m["successes"].items()}

    def mcnemar_exact(b: int, c: int) -> float:
        """Two-sided exact McNemar: a binomial test on the discordant pairs against
        p = 0.5. The concordant cells carry no information about which setup is
        better, so they are not in the test."""
        if b + c == 0:
            return 1.0
        return float(binomtest(b, b + c, 0.5, alternative="two-sided").pvalue)

    def paired(per_email: Any, name: str) -> dict[str, Any] | None:
        """The 2x2 agreement matrix under one per-email notion of "correct", or None
        unless exactly two setups were scored."""
        if len(labels) != 2:
            return None
        first, second = labels
        a_ok, b_ok = per_email(first), per_email(second)
        shared = sorted(set(a_ok) & set(b_ok))
        cell = lambda x, y: [i for i in shared if a_ok[i] is x and b_ok[i] is y]  # noqa: E731
        both, only_a, only_b, neither = (
            cell(True, True),
            cell(True, False),
            cell(False, True),
            cell(False, False),
        )
        return {
            "name": name,
            "a": first["label"],
            "b": second["label"],
            "runs": first["runs"],
            "emails": len(shared),
            "both": both,
            "only_a": only_a,
            "only_b": only_b,
            "neither": neither,
            "p": mcnemar_exact(len(only_a), len(only_b)),
        }

    return majority_by_email, paired, unanimous_by_email


@app.cell
def _(cost_stats_view, majority_by_email, mo, paired, unanimous_by_email):
    def matrix_table(m: dict) -> str:
        return "\n".join(
            [
                f"| | {m['b']} ✅ | {m['b']} ❌ | total |",
                "| --- | ---: | ---: | ---: |",
                f"| **{m['a']} ✅** | {len(m['both'])} | {len(m['only_a'])} | "
                f"{len(m['both']) + len(m['only_a'])} |",
                f"| **{m['a']} ❌** | {len(m['only_b'])} | {len(m['neither'])} | "
                f"{len(m['only_b']) + len(m['neither'])} |",
                f"| **total** | {len(m['both']) + len(m['only_b'])} | "
                f"{len(m['only_a']) + len(m['neither'])} | {m['emails']} |",
            ]
        )

    def verdict(m: dict) -> str:
        b, c, p = len(m["only_a"]), len(m["only_b"]), m["p"]
        better = m["a"] if b > c else m["b"]
        call = (
            f"**p = {p:.3f}** — the {b}:{c} split is within what coin flips produce, so this "
            f"run does not separate the two setups."
            if p >= 0.05
            else f"**p = {p:.3f}** — {better} is ahead by more than chance on this dataset."
        )
        return f"McNemar exact, two-sided, on the {b + c} discordant emails " f"({b} + {c}). {call}"

    def discordant_ids(m: dict) -> str:
        rows = [
            (f"{m['a']} only", m["only_a"]),
            (f"{m['b']} only", m["only_b"]),
            ("neither", m["neither"]),
        ]
        return "\n".join(
            [
                "| Right on | n | emails |",
                "| --- | ---: | --- |",
                *(
                    f"| {name} | {len(ids)} | {', '.join(f'`{i}`' for i in ids) or '—'} |"
                    for name, ids in rows
                ),
            ]
        )

    def column(m: dict, caption: str) -> object:
        return mo.vstack(
            [
                mo.md(f"**{m['name']}** — {caption}"),
                mo.md(matrix_table(m)),
                mo.md(verdict(m)),
                mo.accordion({"Which emails split them": mo.md(discordant_ids(m))}),
            ],
            gap=0.5,
        )

    majority = paired(majority_by_email, "majority")
    unanimous = paired(unanimous_by_email, "pass^n")
    analysis_view = (
        mo.callout(
            mo.md("Pairwise analysis needs exactly two scored setups."),
            kind="warn",
        )
        if majority is None or unanimous is None
        else mo.vstack(
            [
                mo.md("### Do the setups actually differ?"),
                mo.hstack(
                    [
                        column(majority, "correct on a strict majority of runs"),
                        column(unanimous, f"correct on all {unanimous['runs']} runs"),
                    ],
                    widths="equal",
                    gap=2,
                    align="start",
                ),
                mo.md(
                    "*Both setups see the same emails, so the comparison is paired and the "
                    "two accuracy figures are not independent samples. Comparing them as if "
                    "they were overstates the evidence: only the emails the setups disagree "
                    "on carry any, and at 50 emails there are rarely many. The concordant "
                    "cells are excluded for that reason, not by oversight.*"
                ),
                mo.md(
                    "*The two columns bracket the same runs: `majority` is each setup with "
                    "n-way voting on top, `pass^n` is each setup unaided and asked to be "
                    "right every time. A gap that only opens under `pass^n` is a consistency "
                    "gap, not an accuracy one. Only k = n is tested — for k < n the per-email "
                    "`pass^k` is `C(cᵢ,k)/C(n,k)`, an average over run subsets rather than the "
                    "binary outcome McNemar pairs on.*"
                ),
                mo.md(
                    "*Read the discordant emails in the deep dive below before trusting the "
                    "headline gap — an item whose gold route is not derivable from "
                    "`ROUTING_POLICY` scores as a coin flip, and a handful of those is the "
                    "whole difference.*"
                ),
            ],
            gap=0.5,
        )
    )

    mo.accordion({"## Analysis": mo.vstack([analysis_view, cost_stats_view], gap=1.5)})
    return


@app.cell
def _(Any, mean, payload):
    # Cost metrics compared per email. `tested` rows get a p-value; the rest are
    # descriptive — testing every correlated metric would invite multiplicity for
    # no extra information.
    COST_METRICS: list[tuple[str, str, bool]] = [
        ("tokens_in_unique", "each prompt token once — perfect-cache floor", True),
        ("tokens_in_cumulative", "every call's prompt summed — no-cache ceiling", True),
        ("tokens_in_no_fetch", "cumulative minus the `fetch` role", True),
        ("tokens_out", "generated tokens", True),
        ("wall_clock_ms", "time in the scored region", True),
        ("read_tool_calls", "context the setup bought", False),
        ("peak_context_tokens", "largest single prompt + generation", False),
    ]

    def _metric(metrics: dict[str, Any], key: str) -> float | None:
        """One email-run's value. `tokens_in_no_fetch` is derived: unlike the unique
        count, cumulative is a per-call sum with parallel roles, so the graph's free
        `get_new_email` can be subtracted out of it."""
        if key != "tokens_in_no_fetch":
            return metrics.get(key)
        roles = metrics.get("call_roles") or []
        prompts = metrics.get("prompt_tokens") or []
        if not roles or not prompts:
            return None
        return sum(p for role, p in zip(roles, prompts, strict=False) if role != "fetch")

    def per_email_cost(setup: int, key: str) -> dict[str, float]:
        """Per-email mean across runs. Averaging is what keeps the pairing honest: the
        runs are not independent samples, so 50 emails is the sample size, not 250
        email-runs."""
        values: dict[str, list[float]] = {}
        for run in payload.get("results", {}).get(str(setup), []):
            for result in run:
                value = _metric(result["metrics"], key)
                if value is not None:
                    values.setdefault(result["email_id"], []).append(value)
        return {email_id: mean(v) for email_id, v in values.items() if v}

    return COST_METRICS, per_email_cost


@app.cell
def _(Any, exp, labels, log, median, per_email_cost, sqrt, wilcoxon):
    def hodges_lehmann(diffs: list[float]) -> tuple[float, float, float]:
        """Median paired difference with a distribution-free 95% CI — the signed-rank
        test's own point estimate, taken over the Walsh averages it ranks."""
        walsh = sorted(
            (diffs[i] + diffs[j]) / 2 for i in range(len(diffs)) for j in range(i, len(diffs))
        )
        n, total = len(diffs), len(walsh)
        k = max(int(total / 2 - 1.96 * sqrt(n * (n + 1) * (2 * n + 1) / 24)), 0)
        return median(walsh), walsh[k], walsh[total - 1 - k]

    def compare_cost(key: str) -> dict[str, Any] | None:
        """Wilcoxon signed-rank on the per-email paired differences, or None when the
        metric is absent. Cost is multiplicative, so the test runs on log differences
        where every value is positive — the null becomes "same cost", not "same
        absolute difference", and the estimate reads as a ratio."""
        if len(labels) != 2:
            return None
        first, second = labels
        a, b = per_email_cost(first["setup"], key), per_email_cost(second["setup"], key)
        shared = sorted(set(a) & set(b))
        if not shared:
            return None
        xa, xb = [a[i] for i in shared], [b[i] for i in shared]
        logged = all(v > 0 for v in xa + xb)
        diffs = (
            [log(x) - log(y) for x, y in zip(xa, xb)] if logged else [x - y for x, y in zip(xa, xb)]
        )
        point, low, high = hodges_lehmann(diffs)
        # An all-zero difference vector has nothing to rank; scipy raises rather than
        # returning 1.0, which is what "the setups are identical here" should mean.
        p = (
            1.0
            if all(d == 0 for d in diffs)
            else float(wilcoxon(diffs, zero_method="pratt", alternative="two-sided").pvalue)
        )
        return {
            "emails": len(shared),
            "mean_a": sum(xa) / len(xa),
            "mean_b": sum(xb) / len(xb),
            "logged": logged,
            "effect": (exp(point), exp(low), exp(high)) if logged else (point, low, high),
            "p": p,
        }

    return (compare_cost,)


@app.cell
def _(COST_METRICS: list[tuple[str, str, bool]], compare_cost, labels, mo):
    def _effect(row: dict) -> str:
        point, low, high = row["effect"]
        if row["logged"]:
            return f"×{point:.2f} ({low:.2f}–{high:.2f})"
        return f"{point:+,.0f} ({low:+,.0f}–{high:+,.0f})"

    def cost_stats_table() -> str:
        first, second = labels
        lines = [
            f"| Metric | {first['label']} | {second['label']} | A/B, median (95% CI) | p |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for key, caption, tested in COST_METRICS:
            row = compare_cost(key)
            if row is None:
                lines.append(f"| `{key}` | — | — | — | — |")
                continue
            effect = _effect(row)
            p = f"{row['p']:.4f}" if tested else "—"
            lines.append(
                f"| `{key}`<br/><sub>{caption}</sub> | {row['mean_a']:,.0f} | "
                f"{row['mean_b']:,.0f} | {effect} | {p} |"
            )
        return "\n".join(lines)

    cost_stats_view = (
        mo.callout(mo.md("Cost comparison needs exactly two scored setups."), kind="warn")
        if len(labels) != 2
        else mo.vstack(
            [
                mo.md("### Does one setup cost more?"),
                mo.md(cost_stats_table()),
                mo.md(
                    "*Per-email means over the runs, paired and compared with Wilcoxon "
                    "signed-rank. Paired because both setups see the same emails, which "
                    "removes the between-email variance an XL email carries in both columns; "
                    "signed-rank rather than a t-test because cost is right-skewed — a loop "
                    "that wanders for six turns is a long tail. The estimate is the "
                    "Hodges–Lehmann median difference, on the log scale where every value is "
                    "positive, so it reads as a ratio: ×1.00 is parity, below 1 means the "
                    "first setup spends less.*"
                ),
                mo.md(
                    "*The first three rows bracket the same input tokens. `tokens_in_unique` "
                    "assumes a perfect cache serves repeats for free; `tokens_in_cumulative` "
                    "assumes no cache at all. A real API charges cache reads at a fraction of "
                    "the input rate and cache writes at a premium, so a bill sits between "
                    "them — and locally, where the KV cache already avoids the re-reading, "
                    "neither is money and `wall_clock_ms` is the metric that costs anything. "
                    "`tokens_in_no_fetch` is the control-flow-clean one: setup 2 dispatches "
                    "`get_new_email` in code, so excluding that role stops the comparison "
                    "crediting the graph for an implementation choice. Agreement across all "
                    "three is the claim worth making; disagreement says the gap is caching "
                    "policy, not control flow.*"
                ),
                mo.md(
                    "*The p-values answer 'is there a difference at all', which a large "
                    "consistent gap makes a foregone conclusion — read the ratio and its "
                    "interval first. Untested rows are reported for context only; they "
                    "correlate with the tested ones, and pricing every correlated metric "
                    "would buy multiplicity rather than information.*"
                ),
            ],
            gap=0.5,
        )
    )
    return (cost_stats_view,)


@app.cell(hide_code=True)
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
                    # Below Note: the note is the answer key, the scenario is the setting.
                    # Reading the scenario after it keeps the answer from framing the setup.
                    + (
                        f"  \n**Scenario** {email.get('scenario') or ''}"
                        if email.get("scenario")
                        else ""
                    )
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
