from __future__ import annotations

from collections import Counter

from src.memory import MemoryFootprint
from src.models import (
    ROUTING_CLASSES,
    ClassCounts,
    Distribution,
    Email,
    ErrorBreakdown,
    LabelMetrics,
    MetricsSummary,
    RunResult,
)


def peak_memory(results: list[RunResult]) -> MemoryFootprint | None:
    """Footprint of the worst email, or None if calibration never produced one."""
    footprints = [r.metrics.memory for r in results if r.metrics.memory is not None]
    if not footprints:
        return None
    return max(footprints, key=lambda f: f.total_bytes)


def summarize(results: list[RunResult]) -> MetricsSummary:
    return MetricsSummary(
        total=len(results),
        tokens_in_cumulative=sum(r.metrics.tokens_in_cumulative for r in results),
        tokens_in_unique=sum(r.metrics.tokens_in_unique for r in results),
        tokens_out=sum(r.metrics.tokens_out for r in results),
        wall_clock_ms=sum(r.metrics.wall_clock_ms for r in results),
        read_tool_calls=sum(r.metrics.read_tool_calls for r in results),
        errors=sum(1 for r in results if r.error is not None),
        peak_context_tokens=max((r.metrics.peak_context_tokens for r in results), default=0),
        memory=peak_memory(results),
    )


class ScoringError(Exception):
    """Results and dataset don't line up — usually the wrong --data for a results file."""


def _tally(counts: ClassCounts, predicted: str, gold: str, cls: str) -> None:
    """Add one email-run to one class's one-vs-rest counts.

    An ``error`` never predicted anything, so it cannot be a tp, and giving it a tn
    would reward failing to decide. It counts against every class instead: fn for the
    class the label owed, fp for the rest (see ClassCounts).
    """
    if predicted == "error":
        if gold == cls:
            counts.fn += 1
        else:
            counts.fp += 1
    elif predicted == cls and gold == cls:
        counts.tp += 1
    elif predicted == cls:
        counts.fp += 1
    elif gold == cls:
        counts.fn += 1
    else:
        counts.tn += 1


def _majority_correct(votes: list[str], gold: str) -> bool:
    """Whether the most common answer across runs matches the label.

    A tie is no consensus, so it is never correct — otherwise a 1-1 split would score
    by whichever run happened to be enumerated first.
    """
    ranked = Counter(votes).most_common()
    if not ranked:
        return False
    top_count = ranked[0][1]
    winners = [vote for vote, count in ranked if count == top_count]
    return len(winners) == 1 and winners[0] == gold


def score_labels(
    emails: list[Email],
    runs: list[list[RunResult]],
    setup: int,
    label: str,
) -> LabelMetrics:
    """Score one setup's routing decisions against the gold labels.

    ``runs`` is one list of results per run, each covering the same emails. Emails the
    setups skipped (empty bodies) never appear in the results and so are not scored.
    """
    gold = {email.id: email for email in emails}
    by_run: list[dict[str, RunResult]] = [{r.email_id: r for r in run} for run in runs]

    scored_ids = [email_id for email_id in (r.email_id for r in runs[0]) if email_id in gold]
    missing = {r.email_id for r in runs[0]} - set(gold)
    if missing:
        raise ScoringError(f"results reference emails absent from the dataset: {sorted(missing)}")

    correct = Distribution()
    decided = Distribution()
    successes = dict.fromkeys(scored_ids, 0)
    by_next_step = {cls: ClassCounts() for cls in ROUTING_CLASSES}
    by_category: dict[str, dict[str, ClassCounts]] = {}
    errors = ErrorBreakdown()

    for run_results in by_run:
        run_correct = 0
        run_decided = 0
        for email_id in scored_ids:
            result = run_results.get(email_id)
            if result is None:
                raise ScoringError(f"{email_id} is missing from at least one run of setup {setup}")
            predicted = result.predicted.next_step
            expected = gold[email_id].label.next_step
            category = gold[email_id].category or "(uncategorized)"

            if predicted == "error":
                errors.total += 1
                kind = result.error.kind if result.error else "unknown"
                errors.by_kind[kind] = errors.by_kind.get(kind, 0) + 1
            else:
                run_decided += 1
            if predicted == expected:
                run_correct += 1
                successes[email_id] += 1

            slice_ = by_category.setdefault(
                category, {cls: ClassCounts() for cls in ROUTING_CLASSES}
            )
            for cls in ROUTING_CLASSES:
                _tally(by_next_step[cls], predicted, expected, cls)
                _tally(slice_[cls], predicted, expected, cls)
        correct.values.append(run_correct)
        decided.values.append(run_decided)

    majority = sum(
        _majority_correct(
            [run[email_id].predicted.next_step for run in by_run], gold[email_id].label.next_step
        )
        for email_id in scored_ids
    )

    return LabelMetrics(
        setup=setup,
        label=label,
        runs=len(runs),
        emails=len(scored_ids),
        correct=correct,
        decided=decided,
        majority_correct=majority,
        successes=successes,
        by_next_step=by_next_step,
        by_category=by_category,
        errors=errors,
    )
