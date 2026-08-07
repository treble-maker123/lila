from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

import click
from rich.console import Console
from rich.table import Table

from src import setup1_react_local, setup2_graph_local
from src.dataset import load_emails
from src.metrics import summarize
from src.models import Email, RunResult, SetupSummary

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
) -> Path:
    def sanitize(name: str) -> str:
        return name.replace(":", "-").replace("/", "-")

    data_stem = Path(data_path).stem
    setups_str = "".join(sorted(active))
    temp_str = f"{temperature:g}"
    think_str = "thinking" if think else "nothinking"
    filename = (
        f"{data_stem}_setup{setups_str}_temp{temp_str}_{think_str}_"
        f"{sanitize(model_local)}_runs{runs}.json"
    )
    return Path(output_dir) / filename


def _warm_up(model: str, ollama_url: str) -> None:
    """Load the model before the timed region.

    Without this the first email of the first setup absorbs the whole model-load
    cost (seconds, for a 9B) and its wall-clock is not comparable to any other
    email's. Warming once matches the deployment being modelled — an agent working
    through an inbox keeps the model resident. If you ever want to measure the
    cold-start-per-email case instead, force it explicitly for *every* email via
    keep_alive=0 rather than letting it land on email #1 alone.
    """
    import ollama

    console.print(f"[dim]Warming up {model}…[/dim]")
    try:
        ollama.Client(host=ollama_url).chat(
            model=model, messages=[{"role": "user", "content": "ok"}]
        )
    except Exception as exc:
        console.print(f"[yellow]Warm-up failed ({type(exc).__name__}: {exc}); continuing.[/yellow]")


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

    out_path = _make_output_path(
        output_dir, data_path, active, temperature, think, model_local, runs
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = out_path.with_suffix(".partial.jsonl")

    if not no_warm_up:
        _warm_up(model_local, ollama_url)

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
                        emails, model_local, ollama_url, temperature, think, checkpoint
                    )
                )

            if "2" in active:
                console.print("\n[cyan]Running setup 2: Graph workflow, local 9B…[/cyan]")
                all_results.setdefault(2, []).append(
                    setup2_graph_local.run(
                        emails, model_local, ollama_url, temperature, think, checkpoint
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
    table.add_column("Errors", justify="right")

    summaries: list[SetupSummary] = []
    for setup_id, run_list in sorted(all_results.items()):
        flat = [r for run_ in run_list for r in run_]
        s = summarize(flat)
        summaries.append(
            SetupSummary(
                setup=setup_id,
                label=LABELS[setup_id],
                runs=len(run_list),
                emails=len(emails),
                tokens_in_cumulative=s.tokens_in_cumulative,
                tokens_in_unique=s.tokens_in_unique,
                tokens_out=s.tokens_out,
                wall_clock_ms=s.wall_clock_ms,
                errors=s.errors,
            )
        )
        table.add_row(
            LABELS[setup_id],
            str(len(run_list)),
            str(len(emails)),
            str(s.tokens_in_cumulative),
            str(s.tokens_in_unique),
            str(s.tokens_out),
            str(s.wall_clock_ms),
            str(s.errors),
        )
    console.print(table)

    payload = {
        "results": {
            str(k): [[r.model_dump() for r in run_] for run_ in v] for k, v in all_results.items()
        },
        "summary": [s.model_dump() for s in summaries],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    partial_path.unlink(missing_ok=True)
    console.print(f"\n[green]Results written to {out_path}[/green]")


if __name__ == "__main__":
    cli()
