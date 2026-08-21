"""Runs the setups over the dataset.

Setups are **interleaved per email** rather than run one after the other, and the
order alternates. Running all of setup 1 and then all of setup 2 tied setup to
position in the schedule: whichever went second inherited a warmer machine, and
setup 1's system prompt is identical on every email, so its first call per email hit
a prefix cache left by the previous email while the graph's email-led prompts never
did. Both effects landed on wall clock as if they were control flow.

Interleaving separates each setup's calls with the other setup's, and the cache is
additionally busted before every unit (one email, one setup). Best effort: with
OLLAMA_NUM_PARALLEL > 1 the buster may land in a different slot than the run.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence

import ollama

from loop_workflow_comparison import setup1_react_local, setup2_graph_local
from loop_workflow_comparison.mcp_server import READ_TOOLS
from loop_workflow_comparison.memory import KVProfile
from loop_workflow_comparison.models import Email, InferenceResult, Metrics, RunConfig, RunResult

RunEmail = Callable[[Email, RunConfig], InferenceResult]

SETUPS: dict[int, RunEmail] = {
    1: setup1_react_local.run_email,
    2: setup2_graph_local.run_email,
}


def _bust_prefix_cache(client: ollama.Client, cfg: RunConfig) -> None:
    """Overwrite the server's cached prefix with unrelated tokens, so no unit inherits
    the previous one's. Outside the timed region."""
    try:
        client.chat(
            model=cfg.model,
            messages=[{"role": "user", "content": f"ignore this {uuid.uuid4().hex}"}],
            options={"temperature": 0.0, "num_predict": 1, "num_ctx": cfg.num_ctx},
        )
    except Exception:
        pass  # A failed buster costs fairness, not correctness; the run continues.


def _to_result(
    setup: int, email: Email, inferred: InferenceResult, elapsed_ms: int, profile: KVProfile | None
) -> RunResult:
    return RunResult(
        setup=setup,
        email_id=email.id,
        predicted=inferred.label,
        metrics=Metrics(
            tokens_in_cumulative=inferred.tokens_in_cumulative,
            tokens_in_unique=inferred.tokens_in_unique,
            tokens_out=inferred.tokens_out,
            wall_clock_ms=elapsed_ms,
            read_tool_calls=sum(1 for i in inferred.invocations if i.name in READ_TOOLS),
            steps=inferred.steps or None,
            prompt_tokens=inferred.prompt_tokens,
            call_roles=inferred.call_roles,
            tokens_out_pre=inferred.tokens_out_pre,
            tokens_out_post=inferred.tokens_out_post,
            tokens_out_split=inferred.tokens_out_split,
            peak_context_tokens=inferred.peak_context_tokens,
            memory=profile.footprint(inferred.peak_context_tokens) if profile else None,
        ),
        error=inferred.error,
        warnings=inferred.warnings,
        invocations=inferred.invocations,
        debug=inferred.debug,
    )


def run_all(
    emails: Sequence[Email],
    setups: Sequence[int],
    cfg: RunConfig,
    runs: int,
    profile: KVProfile | None,
    on_result: Callable[[int, RunResult], None] | None = None,
) -> dict[int, list[list[RunResult]]]:
    """Returns results[setup_id][run_index] -> the run's results, in email order."""
    client = ollama.Client(host=cfg.ollama_url)
    scored = [e for e in emails if e.body.strip()]
    for skipped in (e for e in emails if not e.body.strip()):
        print(f"skipped empty email {skipped.id}")

    out: dict[int, list[list[RunResult]]] = {s: [[] for _ in range(runs)] for s in setups}
    for run_idx in range(runs):
        for position, email in enumerate(scored):
            # Alternate which setup sees the email first, so neither is always the one
            # paying to re-warm the machine on a given email.
            order = setups if (run_idx + position) % 2 == 0 else list(reversed(setups))
            for setup in order:
                _bust_prefix_cache(client, cfg)
                t0 = time.monotonic()
                inferred = SETUPS[setup](email, cfg)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                result = _to_result(setup, email, inferred, elapsed_ms, profile)
                suffix = f" [{inferred.error.kind}]" if inferred.error else ""
                steps = f" {inferred.steps} step(s)" if setup == 1 else ""
                print(f"[setup{setup}] {email.id}:{steps} {result.predicted.next_step}{suffix}")
                out[setup][run_idx].append(result)
                if on_result is not None:
                    on_result(run_idx, result)
    return out
