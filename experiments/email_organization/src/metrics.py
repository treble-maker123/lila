from __future__ import annotations

from collections import Counter

from src.models import MetricsSummary, RunResult


def summarize(results: list[RunResult]) -> MetricsSummary:
    return MetricsSummary(
        total=len(results),
        tokens_in_cumulative=sum(r.metrics.tokens_in_cumulative for r in results),
        tokens_in_unique=sum(r.metrics.tokens_in_unique for r in results),
        tokens_out=sum(r.metrics.tokens_out for r in results),
        wall_clock_ms=sum(r.metrics.wall_clock_ms for r in results),
        errors=sum(1 for r in results if r.error is not None),
    )


def agreement_rate(all_runs: list[list[RunResult]]) -> float:
    """
    Given N runs each covering the same emails (in order), compute the fraction
    of emails where all runs agree on the routing decision (next_step).
    """
    if not all_runs:
        return 0.0
    n_emails = len(all_runs[0])
    agreed = 0
    for i in range(n_emails):
        votes = [r[i].predicted.next_step for r in all_runs]
        most_common_count = Counter(votes).most_common(1)[0][1]
        if most_common_count == len(all_runs):
            agreed += 1
    return agreed / n_emails if n_emails else 0.0
