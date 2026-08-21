"""Memory footprint of a setup, measured against the live Ollama server.

What differs between the loop and the graph is *how much context they hold at
once*, so that is what this measures. Ollama's ``/api/ps`` cannot show it directly:
the runner reserves weights plus the KV cache for the whole ``num_ctx`` at load
time, so its reported size is a constant of the deployment and reads identically
for both setups. It is the wrong instrument for this question.

Instead, calibrate the server once — load the model at two different ``num_ctx``
values and read the size it reserves for each — and the slope is the exact cost of
one KV slot:

    bytes_per_token = (size(ctx_hi) - size(ctx_lo)) / (ctx_hi - ctx_lo)
    weights_bytes   = size(ctx_lo) - ctx_lo * bytes_per_token

Multiplying by each setup's peak context occupancy (see ``peak_context_tokens``)
gives the memory that setup actually needs. Calibrating rather than deriving from
``/api/show`` keeps this correct for architectures where not every layer holds a
full KV cache, which the config fields do not reveal.
"""

from __future__ import annotations

import httpx
import ollama
from pydantic import BaseModel

# Context sizes used for calibration. Far enough apart that fixed per-load overhead
# is negligible in the slope, small enough that both loads fit anywhere the run
# itself fits.
CALIBRATION_CTX_LO = 2048
CALIBRATION_CTX_HI = 16384


class KVProfile(BaseModel):
    """Per-token KV cost and fixed weight cost of one loaded model."""

    model: str
    bytes_per_token: float
    weights_bytes: int

    def footprint(self, peak_context_tokens: int) -> MemoryFootprint:
        kv_bytes = int(peak_context_tokens * self.bytes_per_token)
        return MemoryFootprint(
            peak_context_tokens=peak_context_tokens,
            kv_bytes=kv_bytes,
            weights_bytes=self.weights_bytes,
            total_bytes=self.weights_bytes + kv_bytes,
        )


class MemoryFootprint(BaseModel):
    """Memory one setup needs at its high-water mark.

    ``kv_bytes`` is the part that differs between setups — the loop carries a
    growing transcript, the graph starts each node fresh. ``weights_bytes`` is
    identical for both and is included only so ``total_bytes`` is the real number.

    This is what the setup *needs*, not what Ollama reserved: with ``num_ctx``
    pinned the server allocates for the full window regardless, so the reserved
    block is an upper bound on every setup alike and says nothing about them.
    """

    peak_context_tokens: int
    kv_bytes: int
    weights_bytes: int
    total_bytes: int


class CalibrationError(Exception):
    """Calibration could not measure the server; the run continues without footprints."""


class OllamaMemoryProfiler:
    """Queries an Ollama server for the memory profile of one model."""

    def __init__(self, ollama_url: str, model: str) -> None:
        self._url = ollama_url.rstrip("/")
        self._model = model
        self._client = ollama.Client(host=ollama_url)

    def calibrate(self) -> KVProfile:
        """Load the model at two context sizes and solve for the per-token KV cost.

        Leaves the model loaded at CALIBRATION_CTX_HI, so callers that care about
        load state (the run's warm-up) must run after this.
        """
        lo = self._reserved_bytes(CALIBRATION_CTX_LO)
        hi = self._reserved_bytes(CALIBRATION_CTX_HI)
        if hi <= lo:
            raise CalibrationError(
                f"reserved size did not grow with num_ctx ({lo} -> {hi}); "
                "the server may be capping num_ctx or reusing a loaded instance"
            )
        bytes_per_token = (hi - lo) / (CALIBRATION_CTX_HI - CALIBRATION_CTX_LO)
        return KVProfile(
            model=self._model,
            bytes_per_token=bytes_per_token,
            weights_bytes=int(lo - CALIBRATION_CTX_LO * bytes_per_token),
        )

    def _reserved_bytes(self, num_ctx: int) -> int:
        """Load the model at ``num_ctx`` and read back what the runner reserved."""
        try:
            self._client.chat(
                model=self._model,
                messages=[{"role": "user", "content": "ok"}],
                options={"num_ctx": num_ctx},
            )
            resp = httpx.get(f"{self._url}/api/ps", timeout=10.0)
            resp.raise_for_status()
            models = resp.json().get("models") or []
        except Exception as exc:
            raise CalibrationError(f"{type(exc).__name__}: {exc}") from exc
        entry = _find_model(models, self._model)
        if entry is None:
            raise CalibrationError(f"{self._model} not loaded after a chat call")
        # A load that did not honour num_ctx would silently flatten the slope.
        loaded_ctx = entry.get("context_length")
        if loaded_ctx is not None and int(loaded_ctx) != num_ctx:
            raise CalibrationError(f"asked for num_ctx={num_ctx}, server loaded {loaded_ctx}")
        return int(entry.get("size") or 0)


def _find_model(models: list[dict], model: str) -> dict | None:
    """Locate the run's model among the loaded ones.

    Ollama echoes the tag under both ``name`` and ``model``; fall back to a prefix
    match so an untagged request like ``qwen3.5`` still resolves.
    """
    for entry in models:
        if model in (entry.get("name"), entry.get("model")):
            return entry
    for entry in models:
        if str(entry.get("model") or entry.get("name") or "").startswith(model):
            return entry
    return None
