"""Shared-prefix accounting for ``tokens_in_unique``.

Measured, not assumed: Ollama's ``prompt_eval_count`` reports the full prompt on
every call regardless of what the KV cache already held (README, "Measuring input
tokens"). So cumulative tokens and peak context are sound, but a setup whose calls
share a prefix must subtract it itself.

The graph is that setup — both node prompts lead with the same email block so the
second node's prefill reuses the first node's cache. Ollama has no tokenizer
endpoint, so the block is measured by difference: a node prompt costs
``HEAD + email + suffix + TAIL``, and sending the suffix alone costs
``HEAD + suffix + TAIL``. The remainder is the email. That is short of the true
shared prefix by HEAD (a few tokens), which errs toward crediting the graph with
more unique input, never less.
"""

from __future__ import annotations

import ollama
from pydantic import BaseModel


class ProbeError(Exception):
    """The probe could not reach the server or got no token count back."""


class PromptProbe(BaseModel):
    """Cost of a node prompt's fixed wrapper, measured once per run."""

    wrapper_tokens: int

    def shared_prefix_tokens(self, node_prompt_tokens: int) -> int:
        """The email block both graph nodes lead with. Clamped so unique can never
        exceed cumulative."""
        return max(0, node_prompt_tokens - self.wrapper_tokens)


def probe_wrapper(client: ollama.Client, model: str, num_ctx: int, suffix: str) -> PromptProbe:
    """One tiny startup call, so it never lands inside a scored email."""
    try:
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": suffix}],
            options={"temperature": 0.0, "num_predict": 1, "num_ctx": num_ctx},
        )
    except Exception as exc:
        raise ProbeError(f"{type(exc).__name__}: {exc}") from exc
    if not resp.prompt_eval_count:
        raise ProbeError("server returned no prompt_eval_count")
    return PromptProbe(wrapper_tokens=resp.prompt_eval_count)
