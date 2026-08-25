"""The value vocabulary shared by every module: what a run may carry, and what YAML gives.

Kept separate from the modules that use it so the lower layers (resources, model) can
speak JSON without importing the executor.
"""

from __future__ import annotations

from datetime import date

# Everything crossing a node boundary is JSON — that is what makes a run recordable and
# replayable. Recursive aliases, so a nested value is checked the same as a top-level one.
type Json = str | int | float | bool | None | list[Json] | dict[str, Json]
type JsonSchema = dict[str, Json]  # a JSON Schema document

# What yaml.safe_load can construct. Wider than JSON — an unquoted date is a date, and
# ``!!binary``/``!!set`` are bytes and sets — so a graph file is narrowed before it is
# compiled, never assumed to be JSON. ``datetime`` is a ``date``, so it needs no arm.
type Yaml = (
    str | int | float | bool | None | date | bytes | list[Yaml] | set[Yaml] | dict[Yaml, Yaml]
)
