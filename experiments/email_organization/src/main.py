from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from src import setup1_react_local, setup2_graph_local, setup3_react_frontier, setup4_voting
from src.dataset import load_emails
from src.metrics import summarize
from src.models import RunResult, SetupSummary

console = Console()


def _make_output_path(
    output_dir: str,
    data_path: str,
    active: set[str],
    temperature: float,
    think: bool,
    model_local: str,
    model_frontier: str,
    runs: int,
) -> Path:
    def sanitize(name: str) -> str:
        return name.replace(":", "-").replace("/", "-")

    data_stem = Path(data_path).stem
    setups_str = "".join(sorted(active))
    temp_str = f"{temperature:g}"
    think_str = "thinking" if think else "nothinking"
    models_str = sanitize(model_local)
    if "3" in active:
        models_str += f"_{sanitize(model_frontier)}"
    filename = (
        f"{data_stem}_setup{setups_str}_temp{temp_str}_{think_str}_{models_str}_runs{runs}.json"
    )
    return Path(output_dir) / filename


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.option(
    "--setup",
    "setups",
    type=click.Choice(["1", "2", "3", "4", "all"]),
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
@click.option("--openrouter-key", envvar="OPENROUTER_API_KEY", default=None)
@click.option("--model-local", default="qwen3.5:9b", show_default=True)
@click.option("--model-frontier", default="anthropic/claude-opus-4-8", show_default=True)
@click.option("--temperature", default=0.0, show_default=True, type=float)
@click.option(
    "--think/--no-think",
    default=False,
    show_default=True,
    help="Enable model thinking/reasoning for Ollama-backed setups (1, 2, 4).",
)
@click.option(
    "--runs",
    default=1,
    show_default=True,
    type=int,
    help="Number of independent trials per setup, each individually recorded.",
)
@click.option(
    "--voting-runs",
    default=5,
    show_default=True,
    type=int,
    help="Number of voting runs per email for setup 4.",
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
def run(
    setups: tuple[str, ...],
    ollama_url: str,
    openrouter_key: str | None,
    model_local: str,
    model_frontier: str,
    temperature: float,
    think: bool,
    runs: int,
    voting_runs: int,
    data_path: str,
    output_dir: str,
) -> None:
    """Run one or more experiment setups against the email dataset."""
    active = {"1", "2", "3", "4"} if "all" in setups else set(setups)

    emails = load_emails(data_path)
    console.print(f"[bold]Loaded {len(emails)} emails from {data_path}[/bold]")

    # all_results[setup_id] = list of runs, each run is list[RunResult]
    all_results: dict[int, list[list[RunResult]]] = {}

    for run_idx in range(runs):
        if runs > 1:
            console.print(f"\n[bold]Run {run_idx + 1}/{runs}[/bold]")

        if "1" in active:
            console.print("\n[cyan]Running setup 1: ReAct loop, local 9B…[/cyan]")
            all_results.setdefault(1, []).append(
                setup1_react_local.run(emails, model_local, ollama_url, temperature, think)
            )

        if "2" in active:
            console.print("\n[cyan]Running setup 2: Graph workflow, local 9B…[/cyan]")
            all_results.setdefault(2, []).append(
                setup2_graph_local.run(emails, model_local, ollama_url, temperature, think)
            )

        if "3" in active:
            if not openrouter_key:
                raise click.UsageError(
                    "--openrouter-key / OPENROUTER_API_KEY required for setup 3."
                )
            console.print("\n[cyan]Running setup 3: ReAct loop, frontier (OpenRouter)…[/cyan]")
            all_results.setdefault(3, []).append(
                setup3_react_frontier.run(emails, model_frontier, openrouter_key, temperature)
            )

        if "4" in active:
            console.print(
                f"\n[cyan]Running setup 4: Voting ({voting_runs}x ReAct loop, local 9B)…[/cyan]"
            )
            all_results.setdefault(4, []).append(
                setup4_voting.run(emails, model_local, ollama_url, temperature, think, voting_runs)
            )

    # Summary table (aggregate across all runs)
    table = Table(title="Results", show_header=True)
    table.add_column("Setup")
    table.add_column("Runs", justify="right")
    table.add_column("Emails", justify="right")
    table.add_column("Tokens In", justify="right")
    table.add_column("Tokens Out", justify="right")
    table.add_column("Wall ms", justify="right")

    labels = {1: "1: ReAct local", 2: "2: Graph local", 3: "3: ReAct frontier", 4: "4: Voting"}
    summaries: list[SetupSummary] = []
    for setup_id, run_list in sorted(all_results.items()):
        flat = [r for run in run_list for r in run]
        s = summarize(flat)
        summaries.append(
            SetupSummary(
                setup=setup_id,
                label=labels[setup_id],
                runs=len(run_list),
                emails=len(emails),
                tokens_in=s.tokens_in,
                tokens_out=s.tokens_out,
                wall_clock_ms=s.wall_clock_ms,
            )
        )
        table.add_row(
            labels[setup_id],
            str(len(run_list)),
            str(len(emails)),
            str(s.tokens_in),
            str(s.tokens_out),
            str(s.wall_clock_ms),
        )
    console.print(table)

    # Write output
    out_path = _make_output_path(
        output_dir, data_path, active, temperature, think, model_local, model_frontier, runs
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "results": {
            str(k): [[r.model_dump() for r in run] for run in v] for k, v in all_results.items()
        },
        "summary": [s.model_dump() for s in summaries],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    console.print(f"\n[green]Results written to {out_path}[/green]")


if __name__ == "__main__":
    cli()
