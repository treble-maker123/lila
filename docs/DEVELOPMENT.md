# Development Guide

## Conventions

Please follow the conventions in this document when developing in this repo.

- After making any changes to Python files, run `make format` from the repo root.
- Always use absolute imports.
- Always specify explicit types on function inputs and outputs. If there are more than one field, model it as a Pydantic class,
- Common commands and scripts should be encapsulated in submodule Makefile. Before running an arbitrary command, check the submodule as well as parent Makefile first.
- Edit files one block at a time. Don't bulk-rewrite a file with a script (python/sed) — those changes are hard to review as they land.
- Keep docs concise. Trim prose to the load-bearing sentence; drop restatements and hedging.
