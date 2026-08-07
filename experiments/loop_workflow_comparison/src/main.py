from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

import click
from rich.console import Console
from rich.table import Table

from src import setup1_react_local, setup2_graph_local
from src.dataset import load_emails
from src.memory import CalibrationError, KVProfile, MemoryFootprint, OllamaMemoryProfiler
from src.metrics import score_labels, summarize
from src.models import ClassCounts, Distribution, Email, LabelMetrics, RunResult, SetupSummary

console = Console()

LABELS = {1: "1: ReAct local", 2: "2: Graph local"}


def _make_output_path(
    output_dir: str,
    data_path: str,
    active: set[str],
    temperature: float,
    think: bool,
    model_local: str,
    runs: int,
    num_ctx: int,
) -> Path:
    def sanitize(name: str) -> str:
        return name.replace(":", "-").replace("/", "-")

    data_stem = Path(data_path).stem
    setups_str = "".join(sorted(active))
    temp_str = f"{temperature:g}"
    think_str = "thinking" if think else "nothinking"
    filename = (
        f"{data_stem}_setup{setups_str}_temp{temp_str}_{think_str}_"
        f"{sanitize(model_local)}_ctx{num_ctx}_runs{runs}.json"
    )
    return Path(output_dir) / filename


def _warm_up(model: str, ollama_url: str, num_ctx: int) -> None:
    """Load the model before the timed region.

    Without this the first email of the first setup absorbs the whole model-load
    cost (seconds, for a 9B) and its wall-clock is not comparable to any other
    email's. Warming once matches the deployment being modelled — an agent working
    through an inbox keeps the model resident. If you ever want to measure the
    cold-start-per-email case instead, force it explicitly for *every* email via
    keep_alive=0 rather than letting it land on email #1 alone.

    The warm-up must use the run's ``num_ctx``: Ollama keys the loaded instance on
    its context size, so warming at a different one reloads on email #1 and wastes
    the warm-up entirely.
    """
    import ollama

    console.print(f"[dim]Warming up {model} (num_ctx={num_ctx})…[/dim]")
    try:
        ollama.Client(host=ollama_url).chat(
            model=model,
            messages=[{"role": "user", "content": "ok"}],
            options={"num_ctx": num_ctx},
        )
    except Exception as exc:
        console.print(f"[yellow]Warm-up failed ({type(exc).__name__}: {exc}); continuing.[/yellow]")


def _calibrate(model: str, ollama_url: str) -> KVProfile | None:
    """Measure the model's per-token KV cost so peaks can be priced in bytes.

    Two extra model loads up front. Failure is not fatal: the run still records
    peak context tokens, which is the same comparison in different units.
    """
    console.print(f"[dim]Calibrating KV cost of {model}…[/dim]")
    try:
        profile = OllamaMemoryProfiler(ollama_url, model).calibrate()
    except CalibrationError as exc:
        console.print(f"[yellow]Calibration failed ({exc}); memory in tokens only.[/yellow]")
        return None
    console.print(
        f"[dim]{profile.bytes_per_token / 1024:.1f} KiB/token, "
        f"weights {profile.weights_bytes / 1e9:.2f} GB[/dim]"
    )
    return profile


def _peak_mb(footprint: MemoryFootprint | None) -> str:
    """Render the footprint as ``total (kv)`` in MB, or '-' if uncalibrated."""
    if footprint is None:
        return "-"
    return f"{footprint.total_bytes / 1e6:.0f} ({footprint.kv_bytes / 1e6:.0f})"


def _pct(numerator: float, denominator: float) -> str:
    return f"{100 * numerator / denominator:.1f}%" if denominator else "-"


def _spread(dist: Distribution) -> str:
    """``mean (min-max, sd)``, dropping the spread when there is only one run."""
    if len(dist.values) < 2:
        return f"{dist.mean:.0f}"
    return f"{dist.mean:.1f} ({dist.minimum}-{dist.maximum}, sd {dist.stdev:.1f})"


def _headline_table(metrics: list[LabelMetrics]) -> Table:
    table = Table(title="Labels", show_header=True)
    for column in ("Setup", "Runs", "Emails", "Correct", "/emails", "/decided", "Majority", "Err"):
        table.add_column(column, justify="right" if column != "Setup" else "left")
    for m in metrics:
        table.add_row(
            m.label,
            str(m.runs),
            str(m.emails),
            _spread(m.correct),
            _pct(m.correct.mean, m.emails),
            _pct(m.correct.mean, m.decided.mean),
            f"{m.majority_correct}/{m.emails}",
            _pct(m.errors.total, m.email_runs),
        )
    return table


def _pass_table(metrics: list[LabelMetrics]) -> Table:
    """pass^k as a k-vs-value curve, one column per setup."""
    table = Table(title="pass^k (all k of k runs correct)", show_header=True)
    table.add_column("k", justify="right")
    for m in metrics:
        table.add_column(m.label, justify="right")
    curves = {m.setup: {p.k: p.value for p in m.pass_curve} for m in metrics}
    for k in range(1, max((m.runs for m in metrics), default=0) + 1):
        table.add_row(
            str(k),
            *(f"{curves[m.setup][k]:.3f}" if k <= m.runs else "-" for m in metrics),
        )
    return table


def _counts_table(
    title: str, slice_header: str | None, rows: list[tuple[str, str, ClassCounts]]
) -> Table:
    """One-vs-rest counts, one row per class. ``slice_header`` names the grouping
    column, or is None when the rows are not sliced."""
    table = Table(title=title, show_header=True)
    if slice_header is not None:
        table.add_column(slice_header)
    table.add_column("Class")
    for column in ("tp", "fp", "fn", "tn", "P", "R", "F1"):
        table.add_column(column, justify="right")
    for slice_name, cls, c in rows:
        table.add_row(
            *([slice_name] if slice_header is not None else []),
            cls,
            str(c.tp),
            str(c.fp),
            str(c.fn),
            str(c.tn),
            f"{c.precision:.2f}",
            f"{c.recall:.2f}",
            f"{c.f1:.2f}",
        )
    return table


def _render_labels(metrics: list[LabelMetrics]) -> None:
    console.print(_headline_table(metrics))
    console.print(_pass_table(metrics))
    for m in metrics:
        console.print(
            _counts_table(
                f"{m.label} — by next_step",
                None,
                [("", cls, counts) for cls, counts in m.by_next_step.items()],
            )
        )
        # Only classes the category actually exercises: a class with no gold email and
        # no prediction in a slice is all tn and says nothing about that email shape.
        rows = [
            (category, cls, counts)
            for category, slice_ in sorted(m.by_category.items())
            for cls, counts in slice_.items()
            if counts.tp or counts.fp or counts.fn
        ]
        console.print(_counts_table(f"{m.label} — by category", "Category", rows))
        if m.errors.by_kind:
            kinds = ", ".join(f"{k}={v}" for k, v in sorted(m.errors.by_kind.items()))
            console.print(f"[yellow]{m.label} errors: {m.errors.total} ({kinds})[/yellow]")


def _checkpoint(fh: TextIO, run_idx: int, result: RunResult) -> None:
    """Append one finished email to the partial-results sidecar.

    A full run is hours of local inference; without this, a crash or a Ctrl-C at
    email 37 of run 3 discards everything, since the consolidated JSON is only
    written at the very end.
    """
    fh.write(json.dumps({"run_index": run_idx, **result.model_dump()}) + "\n")
    fh.flush()


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.option(
    "--setup",
    "setups",
    type=click.Choice(["1", "2", "all"]),
    multiple=True,
    required=True,
    help="Which setup(s) to run. Pass multiple times or use 'all'.",
)
@click.option(
    "--ollama-url",
    envvar="OLLAMA_URL",
    default="http://host.docker.internal:11434",
    show_default=True,
)
@click.option("--model-local", default="qwen3.5:9b", show_default=True)
@click.option("--temperature", default=0.0, show_default=True, type=float)
@click.option(
    "--num-ctx",
    default=32768,
    show_default=True,
    type=int,
    help=(
        "Ollama context window. Pinned well above the 4K default so the loop's growing "
        "transcript is not silently truncated and both setups get the same KV budget."
    ),
)
@click.option(
    "--think/--no-think",
    default=False,
    show_default=True,
    help="Enable model thinking/reasoning.",
)
@click.option(
    "--runs",
    default=1,
    show_default=True,
    type=int,
    help="Number of independent trials per setup, each individually recorded.",
)
@click.option(
    "--data",
    "data_path",
    default="datasets/emails_smoke.json",
    show_default=True,
    type=click.Path(),
)
@click.option(
    "--output-dir",
    default="results",
    show_default=True,
    type=click.Path(),
    help="Directory to write results into.",
)
@click.option(
    "--no-warm-up",
    is_flag=True,
    default=False,
    help="Skip the warm-up call, letting model load time land on the first email.",
)
def run(
    setups: tuple[str, ...],
    ollama_url: str,
    model_local: str,
    temperature: float,
    num_ctx: int,
    think: bool,
    runs: int,
    data_path: str,
    output_dir: str,
    no_warm_up: bool,
) -> None:
    """Run one or more experiment setups against the email dataset."""
    active = {"1", "2"} if "all" in setups else set(setups)

    emails: list[Email] = load_emails(data_path)
    console.print(f"[bold]Loaded {len(emails)} emails from {data_path}[/bold]")
    skipped_email_ids = [email.id for email in emails if not email.body.strip()]

    out_path = _make_output_path(
        output_dir, data_path, active, temperature, think, model_local, runs, num_ctx
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = out_path.with_suffix(".partial.jsonl")

    # Calibration reloads the model at its own context sizes, so it must run before
    # the warm-up leaves the model loaded at the run's num_ctx.
    profile = _calibrate(model_local, ollama_url)

    if not no_warm_up:
        _warm_up(model_local, ollama_url, num_ctx)

    # all_results[setup_id] = list of runs, each run is list[RunResult]
    all_results: dict[int, list[list[RunResult]]] = {}

    with partial_path.open("w") as fh:
        for run_idx in range(runs):
            if runs > 1:
                console.print(f"\n[bold]Run {run_idx + 1}/{runs}[/bold]")

            def checkpoint(result: RunResult, _run_idx: int = run_idx) -> None:
                _checkpoint(fh, _run_idx, result)

            if "1" in active:
                console.print("\n[cyan]Running setup 1: ReAct loop, local 9B…[/cyan]")
                all_results.setdefault(1, []).append(
                    setup1_react_local.run(
                        emails,
                        model_local,
                        ollama_url,
                        temperature,
                        think,
                        num_ctx,
                        profile,
                        checkpoint,
                    )
                )

            if "2" in active:
                console.print("\n[cyan]Running setup 2: Graph workflow, local 9B…[/cyan]")
                all_results.setdefault(2, []).append(
                    setup2_graph_local.run(
                        emails,
                        model_local,
                        ollama_url,
                        temperature,
                        think,
                        num_ctx,
                        profile,
                        checkpoint,
                    )
                )

    # Summary table (aggregate across all runs)
    table = Table(title="Results", show_header=True)
    table.add_column("Setup")
    table.add_column("Runs", justify="right")
    table.add_column("Emails", justify="right")
    table.add_column("Tok In (cum)", justify="right")
    table.add_column("Tok In (uniq)", justify="right")
    table.add_column("Tokens Out", justify="right")
    table.add_column("Wall ms", justify="right")
    table.add_column("Peak ctx", justify="right")
    table.add_column("Peak MB", justify="right")
    table.add_column("Errors", justify="right")

    summaries: list[SetupSummary] = []
    for setup_id, run_list in sorted(all_results.items()):
        flat = [r for run_ in run_list for r in run_]
        s = summarize(flat)
        emails_run = len(run_list[0]) if run_list else 0
        summaries.append(
            SetupSummary(
                setup=setup_id,
                label=LABELS[setup_id],
                runs=len(run_list),
                emails=emails_run,
                tokens_in_cumulative=s.tokens_in_cumulative,
                tokens_in_unique=s.tokens_in_unique,
                tokens_out=s.tokens_out,
                wall_clock_ms=s.wall_clock_ms,
                errors=s.errors,
                peak_context_tokens=s.peak_context_tokens,
                memory=s.memory,
            )
        )
        table.add_row(
            LABELS[setup_id],
            str(len(run_list)),
            str(emails_run),
            str(s.tokens_in_cumulative),
            str(s.tokens_in_unique),
            str(s.tokens_out),
            str(s.wall_clock_ms),
            str(s.peak_context_tokens),
            _peak_mb(s.memory),
            str(s.errors),
        )
    console.print(table)
    if skipped_email_ids:
        console.print(
            f"[yellow]Skipped {len(skipped_email_ids)} empty emails; "
            f"ran {len(emails) - len(skipped_email_ids)} of {len(emails)} per setup.[/yellow]"
        )
        console.print(f"[dim]Skipped IDs: {', '.join(skipped_email_ids)}[/dim]")

    label_metrics = [
        score_labels(emails, run_list, setup_id, LABELS[setup_id])
        for setup_id, run_list in sorted(all_results.items())
    ]

    payload = {
        "results": {
            str(k): [[r.model_dump() for r in run_] for run_ in v] for k, v in all_results.items()
        },
        "summary": [s.model_dump() for s in summaries],
        "labels": [m.model_dump() for m in label_metrics],
        "skipped": {
            "empty_email_ids": skipped_email_ids,
            "total": len(emails),
            "ran": len(emails) - len(skipped_email_ids),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2))
    partial_path.unlink(missing_ok=True)
    console.print(f"\n[green]Results written to {out_path}[/green]")

    _render_labels(label_metrics)


def _infer_data_path(results_path: str) -> str:
    """Recover the dataset a results file was produced from.

    The dataset stem is the filename prefix written by _make_output_path, so scoring
    normally needs only the results file. --data overrides it.
    """
    return f"datasets/{Path(results_path).stem.split('_setup')[0]}.json"


@cli.command()
@click.option("--results", "results_path", required=True, type=click.Path(exists=True))
@click.option(
    "--data",
    "data_path",
    default=None,
    type=click.Path(),
    help="Dataset holding the gold labels. Defaults to the one named in the results filename.",
)
def score(results_path: str, data_path: str | None) -> None:
    """Score a finished results file against its labels, without re-running inference."""
    data_path = data_path or _infer_data_path(results_path)
    emails = load_emails(data_path)
    payload = json.loads(Path(results_path).read_text())
    console.print(f"[bold]Scoring {results_path} against {data_path}[/bold]")

    metrics: list[LabelMetrics] = []
    for setup_str, run_list in sorted(payload["results"].items()):
        setup_id = int(setup_str)
        runs = [[RunResult.model_validate(r) for r in run_] for run_ in run_list]
        metrics.append(score_labels(emails, runs, setup_id, LABELS[setup_id]))
    _render_labels(metrics)

    # Fold the scores back into the results file, replacing any earlier "labels".
    # Captured outputs are untouched, so re-scoring is repeatable and non-destructive.
    payload["labels"] = [m.model_dump() for m in metrics]
    Path(results_path).write_text(json.dumps(payload, indent=2))
    console.print(f"[green]Scores written to {results_path}[/green]")


if __name__ == "__main__":
    cli()
