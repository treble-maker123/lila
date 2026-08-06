from __future__ import annotations

from collections import Counter

from src.models import Label, MetricsSummary, RunResult


def label_correct(predicted: Label, ground_truth: Label) -> bool:
    return (
        predicted.classification == ground_truth.classification
        and predicted.next_step == ground_truth.next_step
    )


def summarize(results: list[RunResult]) -> MetricsSummary:
    total = len(results)
    correct = sum(r.metrics.correct for r in results)
    return MetricsSummary(
        total=total,
        accuracy=correct / total if total else 0.0,
        tokens_in=sum(r.metrics.tokens_in for r in results),
        tokens_out=sum(r.metrics.tokens_out for r in results),
        wall_clock_ms=sum(r.metrics.wall_clock_ms for r in results),
    )


def agreement_rate(all_runs: list[list[RunResult]]) -> float:
    """
    Given N runs each covering the same emails (in order), compute the fraction
    of emails where all runs agree on (classification, next_step).
    """
    if not all_runs:
        return 0.0
    n_emails = len(all_runs[0])
    agreed = 0
    for i in range(n_emails):
        votes = [(r[i].predicted.classification, r[i].predicted.next_step) for r in all_runs]
        most_common_count = Counter(votes).most_common(1)[0][1]
        if most_common_count == len(all_runs):
            agreed += 1
    return agreed / n_emails if n_emails else 0.0
