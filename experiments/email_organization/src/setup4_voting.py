"""Setup 4: Voting — run ReAct local N times, pick plurality per email."""

from __future__ import annotations

import time
from collections import Counter
from typing import TypeVar

from src import setup1_react_local
from src.models import (
    Debug,
    Email,
    Label,
    LoopDebug,
    Metrics,
    RunResult,
    ToolInvocation,
    VotedInferenceResult,
)

T = TypeVar("T")


def _plurality(values: list[T]) -> T:
    return Counter(values).most_common(1)[0][0]


def run_email_voted(
    email: Email, model: str, ollama_url: str, temperature: float, think: bool, n_runs: int
) -> VotedInferenceResult:
    all_labels: list[Label] = []
    all_invocations: list[list[ToolInvocation]] = []
    all_loops: list[LoopDebug] = []
    total_in = total_out = total_steps = 0
    for _ in range(n_runs):
        inferred = setup1_react_local.run_email(email, model, ollama_url, temperature, think)
        all_labels.append(inferred.label)
        all_invocations.append(inferred.invocations)
        all_loops.extend(inferred.debug.loops)
        total_in += inferred.tokens_in
        total_out += inferred.tokens_out
        total_steps += inferred.steps

    next_step = _plurality([l.next_step for l in all_labels])

    # Use actions from the first run that landed on the plurality next_step.
    matching = [l for l in all_labels if l.next_step == next_step]
    best = matching[0] if matching else all_labels[0]

    agreement = sum(1 for l in all_labels if l.next_step == next_step) / n_runs

    return VotedInferenceResult(
        label=Label(
            actions=best.actions,
            next_step=next_step,
            draft=best.draft if next_step == "reply" else None,
        ),
        tokens_in=total_in,
        tokens_out=total_out,
        steps=total_steps,
        agreement=agreement,
        invocations=all_invocations,
        debug=Debug(loops=all_loops),
    )


def run(
    emails: list[Email],
    model: str,
    ollama_url: str,
    temperature: float,
    think: bool,
    n_runs: int = 5,
) -> list[RunResult]:
    results = []
    for email in emails:
        t0 = time.monotonic()
        voted = run_email_voted(email, model, ollama_url, temperature, think, n_runs)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(
            f"[setup4] {email.id}: {voted.steps} ReAct loop iteration(s) "
            f"across {n_runs} voters (agreement {voted.agreement:.0%})"
        )
        results.append(
            RunResult(
                setup=4,
                email_id=email.id,
                predicted=voted.label,
                metrics=Metrics(
                    tokens_in=voted.tokens_in,
                    tokens_out=voted.tokens_out,
                    wall_clock_ms=elapsed_ms,
                    steps=voted.steps,
                ),
                debug=voted.debug,
            )
        )
    return results
