# Development Guide

## Conventions

Please follow the conventions in this document when developing in this repo.

- After making any changes to Python files, run `make ci` from the repo root.
- Always use absolute imports.
- Always specify explicit types on function inputs and outputs. If there are more than one field, model it as a class. See [Typing](#typing).
- Product code uses stdlib dataclasses, not Pydantic. Pydantic is allowed only in throw-away code off the user path — experiments, scripts, and one-off analysis.
- Common commands and scripts should be encapsulated in submodule Makefile. Before running an arbitrary command, check the submodule as well as parent Makefile first.
- Never pin or bound dependency versions in `pyproject.toml`. `uv.lock` already records the exact resolved versions, so constraints add nothing to traceability and only block resolution. Add a bound only when a specific version is known to be broken, with a comment saying why.
- Declare dependencies in the `pyproject.toml` of the package that uses them, not the workspace root. Test dependencies belong to the package's own `dev` group.
- Edit files one block at a time. Don't bulk-rewrite a file with a script (python/sed) — those changes are hard to review as they land.
- Keep docs concise. Trim prose to the load-bearing sentence; drop restatements, hedging, and anything the reader can't act on. State each fact once and reference it from elsewhere.

## Typing

Names and values both get a specific type. A reader should never have to guess what a
`str` holds or what a `dict` may contain.

- **No `Any`, and no `object` in its place.** Both erase the same information. Narrow at
  the boundary with `isinstance` or a `TypeGuard` and carry a real type inward. `Any` is
  allowed only where a library returns it and the value is checked on the next line.
- **Alias every domain `str`.** `type NodeId = str`, `type SlotName = str` — a plain PEP
  695 alias with a trailing comment saying what belongs in it. Not `NewType`: these come
  from parsed files, and wrapping at every field buys nothing. Not `Annotated`: it reads
  as metadata the checker enforces, and it doesn't.
- **Use a `Literal` where the set is closed** (`NodeType`), and key dispatch tables on it
  so a typo fails the typecheck instead of silently never matching.
- **Values speak the vocabulary in `lila.values`.** `Json` is anything crossing a node
  boundary — that is what makes a run recordable. `Yaml` is what `yaml.safe_load` can
  build, which is wider (dates, binary, sets), so a graph file is narrowed to `Json`
  during load rather than assumed. Modules add their own where they need one, e.g.
  `Compiled` in `lila.executor`.
- **Recursive aliases are invariant**, so `list[str]` is not a `list[Json]`. Annotate the
  local (`ids: list[Json] = [...]`) rather than casting.
- Own each name in the lowest module that needs it and import it from there;
  basedpyright rejects a re-export through an intermediate module.

## Tests

### Unit tests

- Live in `tst/`. No network, no daemon, no container — they must pass anywhere.
- Name them `test_<method>__<should-happen-when-doing-scenario>`, so a failure reads as a sentence.
- Keep each case small and targeted: one scenario, one behavior.
- Structure every case in three sections, marked with `# prepare`, `# act`, `# verify` comments. When the assertion is the call itself (e.g. `pytest.raises`), use `# act / verify`.
- Fake the boundary rather than the code under test — `httpx.MockTransport` for HTTP backends.
- Group cases by the method under test, wrapped in `# region <name>` / `# endregion` markers so editors can fold them. Shared fixtures and test doubles go in a leading `# region fixtures`.
- Prefer factory fixtures (a fixture returning a builder) over module-level helpers, so setup that allocates resources can release them on teardown.

### Integration tests

- Live in `integ/`. They exercise the public API against real dependencies.
- Keep them minimal — enough to prove the wiring works, not to re-cover unit-tested branches.
- Skip, never fail, when a dependency is absent, so the suite stays runnable on any machine.
- Point them at dependencies via environment variables (e.g. `LILA_OLLAMA_HOST`).

### Running

```sh
make test-unit                        # unit tests, every package under src/
make test-integ                       # integration tests
make test                             # both
make -C src/core test                 # scope to one package
make -C src/core docker-test          # both, in a container
make -C src/core docker-test-unit     # unit tests only, in a container
make -C src/core docker-test-integ    # integration tests only, in a container
make ci                               # format-check, typecheck, and test
```

`make ci` is the full gate: it runs `format-check` (black/ruff report without rewriting), `typecheck`, then `test`.

Retarget the integration tests with `LILA_OLLAMA_HOST` and `LILA_OLLAMA_MODEL`:

```sh
make -C src/core test-integ LILA_OLLAMA_MODEL=qwen3:8b
```

### Manual testing

Runs a skill against what `.lila/config.toml` names, so it needs real credentials and a
running ollama. `--input` and `--arg` values are text; `--input-json` takes structure.

```sh
uv run lila check .lila/skills/test/email-digest/skill.yaml   # static check, no run
uv run lila run morning-digest --record .lila/records/digest.json   # digest 10 unread; read-only
uv run lila run deploy-notes --input note="deploy is green"         # shape a note, post it
uv run lila call gmail-personal.list_messages --arg unread=true --arg limit=5
uv run lila call gmail-personal.get_message --arg id=1234
uv run lila call discord-alerts.post_message --arg content="hello from lila"
```
